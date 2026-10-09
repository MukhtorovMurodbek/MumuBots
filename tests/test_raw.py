"""Camera raw photos: developed, turned the right way up, and sized for a small machine.

    python3 tests/test_raw.py

ConvertBot reads CR2, CR3, NEF, ARW, DNG, RAF, ORF and RW2 with rawpy (LibRaw)
and writes JPG, PNG, WEBP, AVIF, HEIC and a 16-bit TIFF (raw_convert.py). No
sample file from a camera is kept in this repository -- they are tens of
megabytes -- so the inputs are DNGs written here, by hand, from a mosaic with a
known gradient: a valid TIFF with the handful of tags LibRaw needs (the
orientation tag among them). They are small, and what comes out of a gradient
can be checked: which way up it is, how big, and that the 16-bit TIFF holds what
LibRaw produced and not an 8-bit picture stretched.

Peak memory of a decode was measured on synthetic 24 to 200 MP DNGs and is
written up in raw_convert.py; that is a measurement, not a check, and is not
repeated here.

Needs rawpy and numpy (the checks say so and skip without them).
"""
import io
import json
import os
import pathlib
import shutil
import struct
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / ".mirror" / "convert_bot") if (HERE / ".mirror").exists()
                else str(HERE.parent / "convert_bot"))
sys.path.insert(1, str(HERE.parent / "shared"))
sys.path.append(str(HERE / "stubs"))             # after site-packages: the real Pillow beats the stand-in
os.environ.setdefault("CBOT_TOKEN", "test")
os.environ.setdefault("CBOT_USERNAME", "test")
os.environ.setdefault("DATABASE_URL", "postgresql://stub")
os.environ["ENV_FILES"] = "off"

import convert_utils as cu  # noqa: E402
import formats  # noqa: E402
import jobs  # noqa: E402
import raw_convert as rc  # noqa: E402

failures = []
WORK = pathlib.Path(tempfile.mkdtemp(prefix="raw-"))

try:
    import numpy as np
    import rawpy
except ImportError:
    np = rawpy = None


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL  ") + name + (f" -- {detail}" if not ok and detail else ""))
    if not ok:
        failures.append(name)


# ---------------------------------------------------------------------------
# A DNG, by hand
# ---------------------------------------------------------------------------

BYTE, ASCII, SHORT, LONG, RATIONAL, SRATIONAL = 1, 2, 3, 4, 5, 10


def make_dng(path, width=96, height=64, orientation=1, seed=1):
    """A Bayer mosaic whose left edge is dark and whose right edge is bright
    (a gradient along x, plus a little noise), as a DNG. `orientation` is the
    TIFF/EXIF tag 274: 6 says "turn this a quarter clockwise to see it as the
    photographer did"."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:height, 0:width]
    base = (xx / width * 3500).astype(np.uint16) + 300
    mosaic = np.clip(base + rng.integers(0, 40, (height, width)), 0, 4095).astype("<u2")
    data = mosaic.tobytes()
    entries = [
        (254, LONG, [0]), (256, LONG, [width]), (257, LONG, [height]), (258, SHORT, [16]), (259, SHORT, [1]),
        (262, SHORT, [32803]), (271, ASCII, b"MumuTest\0"), (272, ASCII, b"Synthetic 1\0"),
        (273, LONG, [0]), (274, SHORT, [orientation]), (277, SHORT, [1]), (278, LONG, [height]),
        (279, LONG, [len(data)]), (284, SHORT, [1]), (33421, SHORT, [2, 2]), (33422, BYTE, bytes([0, 1, 1, 2])),
        (50706, BYTE, bytes([1, 4, 0, 0])), (50707, BYTE, bytes([1, 1, 0, 0])),
        (50708, ASCII, b"MumuTest Synthetic 1\0"), (50714, LONG, [0]), (50717, LONG, [4095]),
        (50721, SRATIONAL, [(1, 1), (0, 1), (0, 1), (0, 1), (1, 1), (0, 1), (0, 1), (0, 1), (1, 1)]),
        (50728, RATIONAL, [(1, 1), (1, 1), (1, 1)]), (50778, SHORT, [21]),
    ]
    entries.sort(key=lambda e: e[0])
    n = len(entries)
    extra_at = 8 + 2 + n * 12 + 4
    extra = bytearray()
    rows = []
    for tag, typ, val in entries:
        if isinstance(val, bytes):
            payload, count = val, len(val)
        else:
            count = len(val)
            payload = b"".join(struct.pack("<H", v) if typ == SHORT else struct.pack("<I", v) if typ == LONG
                               else struct.pack("<ii" if typ == SRATIONAL else "<II", *v) for v in val)
        if len(payload) > 4:
            rows.append([tag, typ, count, extra_at + len(extra), None])
            extra += payload + (b"\0" if len(payload) % 2 else b"")
        else:
            rows.append([tag, typ, count, None, payload.ljust(4, b"\0")])
    data_at = extra_at + len(extra)
    out = bytearray(b"II*\0" + struct.pack("<I", 8) + struct.pack("<H", n))
    for tag, typ, count, where, inline in rows:
        field = struct.pack("<I", data_at) if tag == 273 else (struct.pack("<I", where) if where is not None else inline)
        out += struct.pack("<HHI", tag, typ, count) + field
    out += struct.pack("<I", 0) + extra + data
    pathlib.Path(path).write_bytes(out)
    return pathlib.Path(path)


def read_tiff16(path):
    """An independent reader of the one shape of TIFF raw_convert writes."""
    raw = pathlib.Path(path).read_bytes()
    assert raw[:4] == b"II*\0"
    (ifd,) = struct.unpack_from("<I", raw, 4)
    (n,) = struct.unpack_from("<H", raw, ifd)
    tags = {}
    for i in range(n):
        tag, typ, count, value = struct.unpack_from("<HHII", raw, ifd + 2 + i * 12)
        tags[tag] = (typ, count, value)
    width, height = tags[256][2], tags[257][2]
    bits = struct.unpack_from("<3H", raw, tags[258][2])
    offset, size = tags[273][2], tags[279][2]
    pixels = np.frombuffer(raw, dtype="<u2", count=width * height * 3, offset=offset).reshape(height, width, 3)
    return {"width": width, "height": height, "bits": bits, "compression": tags[259][2], "photometric": tags[262][2],
            "samples": tags[277][2], "size": size, "offset": offset, "file": len(raw), "pixels": pixels,
            "icc": tags.get(34675)}


def matrix():
    print("=== what is offered ===")
    formats.probe()
    check("the eight raw formats are image sources", all(formats.category_of(e) == formats.IMAGE for e in formats.RAW_FORMATS))
    check("... and are offered exactly when rawpy is here",
          all(formats.is_supported_source(e) == (rawpy is not None) for e in formats.RAW_FORMATS))
    want = ["jpg", "png", "webp", "avif", "heic", "tiff"]
    if rawpy is not None:
        for e in formats.RAW_FORMATS:
            got = formats.targets_for(e)
            check(f".{e} offers JPG first, and no PDF, ICO or animation",
                  got[:1] == ["jpg"] and "pdf" not in got and "ico" not in got and "gif" not in got, str(got))
        check("every target is one the converter writes", all(t in rc.WRITES for t in formats.declared_targets("dng")))
    saved = set(formats._MET)
    formats._MET.discard("rawpy")
    check("without rawpy nothing raw is offered or taken", not any(formats.is_supported_source(e) for e in formats.RAW_FORMATS)
          and not formats.targets_for("dng"))
    check("... and ordinary pictures are unaffected", "pdf" in formats.targets_for("png"))
    formats._MET.clear()
    formats._MET.update(saved)
    check("a raw photo is not offered itself", all(e not in formats.declared_targets(e) for e in formats.RAW_FORMATS))
    check("find_format knows the maker's names", formats.find_format("cr3") == "cr3" and formats.find_format(".NEF") == "nef")
    check("the declared targets are the six", list(formats.declared_targets("cr2")) == want)

    print("\n=== what a raw conversion costs ===")
    mb = 1024 * 1024
    plain, _ = cu.price_for_job("jpg", "jpg", 25 * mb)
    raw_price, out = cu.price_for_job("dng", "jpg", 25 * mb)
    check("a 25 MB raw file costs more than a 25 MB JPEG: the decode is real work", raw_price >= plain
          and cu.work_factor("dng", "jpg") == cu.RAW_WORK_FACTOR * cu.work_factor("jpg", "jpg"))
    check("a raw file's quote is an estimate of the developed picture, by the raw table (a JPEG of a raw file is a quarter of it)",
          cu.estimate_output_bytes("dng", "jpg", 25 * mb) == int(25 * mb * 0.25)
          and cu.estimate_output_bytes("jpg", "webp", 25 * mb) != cu.estimate_output_bytes("dng", "webp", 25 * mb))
    check("TIFF from a raw file is the biggest thing it makes",
          cu.estimate_output_bytes("dng", "tiff", 25 * mb) > cu.estimate_output_bytes("dng", "png", 25 * mb)
          > cu.estimate_output_bytes("dng", "jpg", 25 * mb))
    flagged_raw = jobs.unsendable_targets(40.0, ["tiff", "png", "jpg"], 30 * mb, 1, raw=True)
    flagged_plain = jobs.unsendable_targets(40.0, ["tiff", "png", "jpg"], 30 * mb, 1, raw=False)
    if jobs.big_sends(1):
        print("  (this host sends over MTProto: the send-limit checks are skipped)")
    else:
        check("a 40 MP 16-bit TIFF (240 MB) is kept off the menu where a 50 MB bot cannot send it",
              "tiff" in flagged_raw and "jpg" not in flagged_raw, str(flagged_raw))
        check("... which an ordinary 40 MP TIFF (compressed, estimated low) is not", "tiff" not in flagged_plain, str(flagged_plain))


def plan():
    print("\n=== what the file's header says, before any decoding ===")
    p = make_dng(WORK / "a.dng", 96, 64)
    info = rc.inspect(p, "dng")
    check("size, megapixels, and no half-size for a small photo",
          (info.width, info.height, info.half) == (96, 64, False) and abs(info.megapixels - 96 * 64 / 1e6) < 1e-9, str(info))
    check("what comes out is the same size", (info.out_width, info.out_height) == (96, 64))
    turned = make_dng(WORK / "turned.dng", 96, 64, orientation=6)
    info = rc.inspect(turned, "dng")
    check("a photo the camera says to turn a quarter reports its sides the other way round",
          (info.width, info.height) == (64, 96), str(info))
    saved = rc.FULL_MP
    rc.FULL_MP = 0.005                         # 5,000 pixels: 96 x 64 = 6,144 is over
    info = rc.inspect(p, "dng")
    check("past FULL_MP the plan is half size, and says what comes out",
          info.half and (info.out_width, info.out_height) == (48, 32) and abs(info.out_megapixels - 48 * 32 / 1e6) < 1e-9, str(info))
    rc.FULL_MP = saved
    check("megapixels() is the OUTPUT size (what the estimates are about)",
          rc.megapixels(p, "dng") == 96 * 64 / 1e6)
    check("and None for a file that is not a raw photo", rc.megapixels(write(WORK / "junk.cr3", os.urandom(2000)), "cr3") is None)
    check("describe() says nothing rather than refusing", rc.describe(WORK / "junk.cr3", "cr3") is None)
    check("inspect() refuses a non-raw file with a sentence", raises(lambda: rc.inspect(WORK / "junk.cr3", "cr3"), cu.ConversionError))


def write(path, data):
    pathlib.Path(path).write_bytes(data)
    return pathlib.Path(path)


def raises(fn, exc):
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


def developed():
    from PIL import Image
    print("\n=== a raw photo comes out as a picture ===")
    src = make_dng(WORK / "d.dng", 96, 64)
    for target in ("jpg", "png", "webp"):
        out = WORK / f"d.{target}"
        cu.convert([src], "dng", target, WORK, "d")
        with Image.open(out) as im:
            im.load()
            size = im.size
            left = np.asarray(im.convert("L"))[:, :10].mean()
            right = np.asarray(im.convert("L"))[:, -10:].mean()
        check(f"dng -> {target}: 96x64, and the gradient runs the way it was built (dark left, bright right)",
              size == (96, 64) and right > left + 40, f"{size} {left:.0f} {right:.0f}")
        out.unlink()
    for target in ("avif", "heic"):
        if target not in formats.targets_for("dng"):
            print(f"  (this Pillow cannot write {target}: skipped)")
            continue
        cu.convert([src], "dng", target, WORK, "d")
        check(f"dng -> {target} is written", (WORK / f"d.{target}").stat().st_size > 0)
        (WORK / f"d.{target}").unlink()

    print("\n=== the right way up ===")
    turned = make_dng(WORK / "t.dng", 96, 64, orientation=6)
    cu.convert([turned], "dng", "png", WORK, "t")
    with Image.open(WORK / "t.png") as im:
        gray = np.asarray(im.convert("L"))
        size = im.size
    top, bottom = gray[:10, :].mean(), gray[-10:, :].mean()
    check("orientation 6 comes out 64 wide and 96 tall", size == (64, 96), str(size))
    check("... turned a quarter clockwise: the dark left edge is now the top", bottom > top + 40, f"{top:.0f} {bottom:.0f}")
    upright = make_dng(WORK / "u.dng", 96, 64, orientation=1)
    cu.convert([upright], "dng", "png", WORK, "u")
    with Image.open(WORK / "u.png") as im:
        check("orientation 1 is left alone", im.size == (96, 64))
    only = sorted(p.name for p in WORK.glob("t.*"))
    check("one output per conversion", only == ["t.dng", "t.png"], str(only))


def tiff16():
    print("\n=== TIFF is 16 bits a channel ===")
    src = make_dng(WORK / "x.dng", 96, 64)
    cu.convert([src], "dng", "tiff", WORK, "x")
    t = read_tiff16(WORK / "x.tiff")
    check("96x64, 16 bits in all three channels, uncompressed RGB",
          (t["width"], t["height"], t["bits"], t["compression"], t["photometric"], t["samples"])
          == (96, 64, (16, 16, 16), 1, 2, 3), str({k: t[k] for k in ("width", "height", "bits")}))
    check("the one strip is exactly the pixels, and the file ends where they do",
          t["size"] == 96 * 64 * 6 and t["offset"] + t["size"] == t["file"], f"{t['offset']} {t['size']} {t['file']}")
    with rawpy.imread(str(src)) as raw:
        expected = raw.postprocess(use_camera_wb=True, no_auto_bright=True, output_bps=16,
                                   output_color=rawpy.ColorSpace.sRGB, demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD)
    check("the pixels are what LibRaw developed, value for value", np.array_equal(t["pixels"], expected))
    check("and they really use 16 bits: values past 255, and not all copies of an 8-bit value",
          int(t["pixels"].max()) > 255 and bool((t["pixels"] % 257 != 0).any()))
    try:
        __import__("PIL.ImageCms")                # only whether it is there
        check("the TIFF says it is sRGB (an editor asks)", t["icc"] is not None and t["icc"][1] > 100, str(t["icc"]))
    except ImportError:
        print("  (no lcms in this Pillow: the profile check is skipped)")
    # the writer on its own, on an array that is not LibRaw's
    gradient = (np.arange(7 * 5 * 3, dtype=np.uint32) * 1871 % 65536).astype(np.uint16).reshape(5, 7, 3)
    rc._write_tiff16(gradient, WORK / "g.tiff")
    check("the writer round-trips an arbitrary 16-bit array exactly", np.array_equal(read_tiff16(WORK / "g.tiff")["pixels"], gradient))
    check("... and refuses what is not 16-bit RGB", raises(lambda: rc._write_tiff16(gradient.astype(np.uint8), WORK / "bad.tiff"), cu.ConversionError))
    from PIL import Image
    try:
        with Image.open(WORK / "x.tiff") as im:
            im.load()
        print("  (this Pillow opens a 16-bit RGB TIFF)")
    except Exception:
        print("  (Pillow cannot open a 16-bit RGB TIFF -- which is why the file is written here and checked by an independent reader)")


def sizes():
    from PIL import Image
    print("\n=== past FULL_MP: half size; past MAX_MP: refused before anything is made ===")
    src = make_dng(WORK / "h.dng", 96, 64)
    saved = (rc.FULL_MP, rc.MAX_MP)
    rc.FULL_MP = 0.005
    cu.convert([src], "dng", "png", WORK, "h")
    with Image.open(WORK / "h.png") as im:
        check("half size comes out 48x32", im.size == (48, 32), str(im.size))
    cu.convert([src], "dng", "tiff", WORK, "h")
    t = read_tiff16(WORK / "h.tiff")
    check("... as a TIFF too", (t["width"], t["height"]) == (48, 32))
    rc.FULL_MP, rc.MAX_MP = 0.001, 0.005
    out = WORK / "big.png"
    started = time.monotonic()
    try:
        cu.convert([src], "dng", "png", WORK, "big")
        check("past MAX_MP the photo is refused", False)
    except cu.ConversionError as exc:
        check("past MAX_MP the photo is refused, with the numbers in the sentence",
              f"{rc._mp(96 * 64 / 1e6)} megapixels" in str(exc) and f"up to {rc._mp(0.005)}" in str(exc), str(exc))
        check("... and a photo's own size is in whole megapixels", rc._mp(24.4) == "24" and rc._mp(0.006) == "0.01")
        check("... quickly, with nothing made", time.monotonic() - started < 5 and not out.exists())
    rc.FULL_MP, rc.MAX_MP = saved


def damaged():
    print("\n=== damaged and mislabelled files ===")
    for name, data in (("random.cr3", os.urandom(5000)), ("empty.nef", b""), ("text.arw", b"not a photo at all" * 50)):
        path = write(WORK / name, data)
        try:
            cu.convert([path], name.rsplit(".", 1)[1], "jpg", WORK, "dmg")
            check(f"{name} is refused", False)
        except cu.ConversionError as exc:
            check(f"{name}: refused with a sentence that names the format", name.rsplit(".", 1)[1].upper() in str(exc), str(exc))
        except Exception as exc:
            check(f"{name}: a ConversionError, not a crash", False, f"{type(exc).__name__}: {exc}")
    good = (WORK / "a.dng").read_bytes()
    path = write(WORK / "cut.dng", good[: len(good) // 2])
    try:
        cu.convert([path], "dng", "jpg", WORK, "cut")
        print("  (LibRaw develops half a file as far as it can: nothing to refuse)")
    except cu.ConversionError:
        check("half a DNG is refused with a sentence", True)
    except Exception as exc:
        check("half a DNG is a ConversionError, not a crash", False, f"{type(exc).__name__}: {exc}")
    # a JPEG called .nef
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 10, 10)).save(buf, "JPEG")
    jpeg = write(WORK / "photo.nef", buf.getvalue())
    try:
        cu.convert([jpeg], "nef", "png", WORK, "jpegnef")
        print("  (LibRaw reads a JPEG called .nef as a picture: nothing to refuse)")
    except cu.ConversionError:
        check("a JPEG called .nef is refused with a sentence", True)

    print("\n=== running out of memory is said as that ===")

    class FakeRaw:
        class sizes:
            width, height, flip = 100, 100, 0

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def postprocess(self, **kw):
            raise MemoryError()
    real_open = rc._open
    rc._open = lambda path, ext: FakeRaw()
    try:
        cu.convert([WORK / "a.dng"], "dng", "jpg", WORK, "oom")
        check("a MemoryError while developing is OutOfMemory (the refund path)", False)
    except cu.OutOfMemory:
        check("a MemoryError while developing is OutOfMemory (the refund path), not 'could not read the file'", True)
    except Exception as exc:
        check("a MemoryError while developing is OutOfMemory", False, f"{type(exc).__name__}")
    finally:
        rc._open = real_open


def describing():
    """The menu's look at a raw photo (its size, before anyone pays) is LibRaw
    parsing a stranger's file, in C. It runs in a child process, started the
    way a conversion is, and anything that goes wrong in it is "unreadable"."""
    import asyncio
    import ast
    import logging

    print("\n=== the size of a raw photo is read without decoding it ===")
    # rawpy.imread always unpacks: the whole raw decoded, ~200 MB for 100 MP.
    import types
    real_rawpy = rc._rawpy

    def no_imread():
        real = real_rawpy()

        def imread(*_a, **_k):
            raise AssertionError("inspect unpacked the photo")
        return types.SimpleNamespace(RawPy=real.RawPy, imread=imread)
    rc._rawpy = no_imread
    try:
        sample = sorted(WORK.glob("*.dng"))
        if sample:
            info = rc.inspect(sample[0], "dng")
            check("inspect reads the size from the header (open_file, never imread/unpack)",
                  info.width > 0 and info.height > 0, repr(info))
        else:
            print("  (no DNG made yet in this run: the no-unpack check is skipped)")
    except Exception as exc:                     # _open turns the AssertionError into a ConversionError
        check("inspect reads the size from the header (open_file, never imread/unpack)", False, str(exc))
    finally:
        rc._rawpy = real_rawpy

    print("\n=== the size of a raw photo is read in a child process ===")
    warnings_seen = []

    class Collect(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.WARNING:
                warnings_seen.append(record.getMessage())
    collector = Collect()
    logging.getLogger("jobs").addHandler(collector)
    original = jobs.WORKER
    real_start = jobs._start_worker

    def ask(path, ext="dng", timeout=None):
        warnings_seen.clear()
        return asyncio.run(jobs.describe_raw(path, ext) if timeout is None else jobs.describe_raw(path, ext, timeout))

    try:
        photo = make_dng(WORK / "child.dng", 96, 64)
        answer = ask(photo)
        check("a real photo is described, as the same RawInfo the in-process reader gives",
              isinstance(answer, rc.RawInfo) and answer == rc.inspect(photo, "dng"), str(answer))
        check("... with nothing logged", not warnings_seen, str(warnings_seen))

        # The numbers cross as JSON, whatever they are: half size, and a turned photo.
        os.environ["CONVERT_RAW_FULL_MP"] = "0.005"
        try:
            answer = ask(photo)
        finally:
            os.environ.pop("CONVERT_RAW_FULL_MP")
        check("a half-size plan crosses intact (bool and floats)",
              answer is not None and answer.half is True and (answer.out_width, answer.out_height) == (48, 32)
              and abs(answer.out_megapixels - 48 * 32 / 1e6) < 1e-12, str(answer))
        turned = make_dng(WORK / "child-turned.dng", 96, 64, orientation=6)
        answer = ask(turned)
        check("a turned photo's sides come the right way round", answer is not None and (answer.width, answer.height) == (64, 96))

        # Not the in-process reader: break it, and the answer is still the real one.
        real_inspect, real_describe = rc.inspect, rc.describe
        rc.inspect = rc.describe = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("in-process reader was used"))
        try:
            answer = ask(photo)
        finally:
            rc.inspect, rc.describe = real_inspect, real_describe
        check("... and the parent's own reader is not what answered", answer is not None and answer.width == 96)

        junk = write(WORK / "sekrit-photo.cr3", os.urandom(3000))
        check("a file that is not a raw photo is None", ask(junk, "cr3") is None)
        check("... and is not a warning (it is an ordinary unreadable file)", not warnings_seen, str(warnings_seen))
        check("a missing file is None", ask(WORK / "nowhere.dng") is None)

        # A child that goes wrong in each way it can. The real worker is swapped for a script.
        started = []

        async def recording_start(payload):
            proc = await real_start(payload)
            started.append(proc)
            return proc
        jobs._start_worker = recording_start

        def with_script(body, **kw):
            script = WORK / "fake_worker.py"
            script.write_text(body)
            jobs.WORKER = script
            started.clear()
            try:
                return ask(WORK / "sekrit-photo.dng", **kw)
            finally:
                jobs.WORKER = original

        good = {"width": 96, "height": 64, "megapixels": 96 * 64 / 1e6, "half": False,
                "out_width": 96, "out_height": 64, "out_megapixels": 96 * 64 / 1e6}
        answer = with_script("import json\nprint(json.dumps({'ok': True, 'info': %r}))\n" % good)
        check("(a script that answers properly is believed, so the cases below are the script's fault)",
              answer == rc.RawInfo(96, 64, 96 * 64 / 1e6, False, 96, 64, 96 * 64 / 1e6), str(answer))
        began = time.monotonic()
        answer = with_script("import time\ntime.sleep(60)\n", timeout=1.0)
        took = time.monotonic() - began
        check("a child that never answers is stopped at the deadline, and the answer is None",
              answer is None and took < 15, f"{answer} after {took:.1f}s")
        check("... the process is dead, not left running", started and started[0].returncode is not None)
        check("... and it is a warning, with no file name in it",
              warnings_seen and not any("sekrit" in line for line in warnings_seen), str(warnings_seen))
        answer = with_script("import sys\nsys.exit(3)\n")
        check("a child that dies without a word is None and a warning",
              answer is None and warnings_seen and not any("sekrit" in line for line in warnings_seen), str(warnings_seen))
        answer = with_script("import os, signal\nos.kill(os.getpid(), signal.SIGTERM if hasattr(signal, 'SIGKILL') else 9)\n")
        check("a child that is killed is None", answer is None)
        for label, line in (("not JSON", "this is not json"), ("a list", "[1, 2, 3]"), ("nothing", ""),
                            ("ok with no info", '{"ok": true}'), ("an empty info", '{"ok": true, "info": {}}'),
                            ("a width of -1", json_with(good, width=-1)), ("a width of 0", json_with(good, height=0)),
                            ("a float width", json_with(good, width=96.5)), ("a boolean width", json_with(good, width=True)),
                            ("a string width", json_with(good, width="96")),
                            ("megapixels that are not width x height", json_with(good, megapixels=5.0)),
                            ("out megapixels that are not out width x height", json_with(good, out_megapixels=5.0)),
                            ("NaN megapixels", '{"ok": true, "info": ' + json.dumps(good).replace(repr(96 * 64 / 1e6), "NaN", 1) + "}"),
                            ("half as a string", json_with(good, half="yes")), ("half as a number", json_with(good, half=1)),
                            ("a failure", '{"ok": false, "kind": "crash", "message": "boom"}')):
            answer = with_script("print(%r)\n" % line)
            check(f"an answer that is {label} is None", answer is None, str(answer))
        # the last line is the answer, as for a conversion: noise before it is ignored
        answer = with_script("import json\nprint('libraw says hello')\nprint(json.dumps({'ok': True, 'info': %r}))\n" % good)
        check("noise before the answer is ignored", answer is not None and answer.width == 96)
    finally:
        jobs._start_worker = real_start
        jobs.WORKER = original
        logging.getLogger("jobs").removeHandler(collector)

    print("\n=== and that is what the bot asks ===")
    bot_source = (HERE.parent / "convert_bot" / "bot.py").read_text(encoding="utf-8")
    tree = ast.parse(bot_source)
    in_process = sorted({f"raw_convert.{node.attr}" for node in ast.walk(tree)
                         if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                         and node.value.id == "raw_convert" and node.attr in ("describe", "inspect", "megapixels", "convert", "_open")})
    check("bot.py never reads a raw file itself (no describe, inspect, megapixels or convert from raw_convert)",
          not in_process, str(in_process))
    asks = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "describe_raw" and isinstance(node.func.value, ast.Name) and node.func.value.id == "jobs"]
    check("... it asks jobs.describe_raw", len(asks) == 1, str(len(asks)))
    jobs_source = (HERE.parent / "convert_bot" / "jobs.py").read_text(encoding="utf-8")
    check("jobs.py starts a child in exactly one place (the C edition's bundler rewrites that one line)",
          jobs_source.count('sys.executable, "-u", str(WORKER), json.dumps(payload),') == 1)


def json_with(base, **changes):
    fields = dict(base)
    fields.update(changes)
    return json.dumps({"ok": True, "info": fields})


def describing_images():
    """A picture's size, read in a child too (jobs.describe_image): some of
    Pillow's header reads decode, and the bot's process is five bots' in C."""
    print("\n=== an ordinary picture's size is read in a child as well ===")
    import ast
    import asyncio
    import tracemalloc
    from PIL import Image
    photo = WORK / "size.jpg"
    Image.new("RGB", (4000, 3000), "white").save(photo, "JPEG")
    # An icon whose directory says 16x16 and whose PNG is 13000x13000: Pillow
    # decodes that PNG inside open() (679 MB, review of 2026-10-08).
    png = io.BytesIO()
    Image.new("1", (13000, 13000)).save(png, "PNG")
    bomb = WORK / "icon-as.jpg"
    bomb.write_bytes(struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", 16, 16, 0, 0, 1, 32,
                                                                  len(png.getvalue()), 22) + png.getvalue())
    junk = WORK / "junk.jpg"
    junk.write_bytes(b"not a picture")
    tracemalloc.start()
    sizes = [asyncio.run(jobs.describe_image(path)) for path in (photo, bomb, junk)]
    peak = tracemalloc.get_traced_memory()[1] / 2**20
    tracemalloc.stop()
    check("a 4000x3000 photo is 12.0 MP", sizes[0] == 12.0, repr(sizes))
    check("an icon hiding a 169 MP PNG is measured as 169 MP, not as its directory's 16x16",
          sizes[1] == 169.0, repr(sizes))
    check("... and the bot's own process never held it", peak < 20, f"{peak:.0f} MB")
    check("something that is not a picture is None", sizes[2] is None, repr(sizes))
    real_start = jobs._start_worker

    async def hangs(payload):
        return await asyncio.create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(60)",
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    jobs._start_worker = hangs
    try:
        started = time.monotonic()
        stuck = asyncio.run(jobs.describe_image(photo, timeout=1))
        check("a child that hangs is killed at the timeout and gives None",
              stuck is None and time.monotonic() - started < 10, repr(stuck))
    finally:
        jobs._start_worker = real_start
    for answer, want in ((b'\n{"ok": true, "width": 10, "height": 20}\n', 0.0002),
                         (b'{"ok": true, "width": -1, "height": 20}', None),
                         (b'{"ok": true, "width": true, "height": 20}', None),
                         (b'{"ok": false, "kind": "refused"}', None), (b"garbage", None), (b"", None)):
        check(f"the answer {answer[:40]!r} reads as {want}", jobs._image_size(answer) == want,
              repr(jobs._image_size(answer)))
    # At most DESCRIBE_AT_ONCE children at a time, however many uploads arrive.
    running, most = 0, 0

    async def counted(payload):
        nonlocal running, most
        running += 1
        most = max(most, running)
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import time; time.sleep(0.4); print('{\"ok\": true, \"width\": 2, \"height\": 2}')",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        real_communicate = proc.communicate

        async def communicate():
            nonlocal running
            try:
                return await real_communicate()
            finally:
                running -= 1
        proc.communicate = communicate
        return proc

    async def album():
        jobs._describe_slots = asyncio.Semaphore(jobs.DESCRIBE_AT_ONCE)      # this loop's own
        return await asyncio.gather(*(jobs.describe_image(photo) for _ in range(6)))
    jobs._start_worker = counted
    try:
        answers = asyncio.run(album())
        check(f"six pictures at once start at most {jobs.DESCRIBE_AT_ONCE} children at a time",
              most == jobs.DESCRIBE_AT_ONCE and answers == [4e-06] * 6, f"{most} at once, {answers}")
    finally:
        jobs._start_worker = real_start
    tree = ast.parse((HERE.parent / "convert_bot" / "bot.py").read_text(encoding="utf-8"))
    opens = [node for node in ast.walk(tree) if isinstance(node, ast.Attribute) and node.attr == "open"
             and isinstance(node.value, ast.Name) and node.value.id == "Image"]
    asks = [node for node in ast.walk(tree) if isinstance(node, ast.Attribute) and node.attr == "describe_image"]
    check("bot.py never opens a picture itself, and asks jobs.describe_image", not opens and len(asks) == 1,
          f"{len(opens)} Image.open, {len(asks)} describe_image")


def main():
    matrix()
    describing_images()
    if rawpy is None:
        print("\n(rawpy or numpy is not installed: the conversions are skipped)")
        return
    plan()
    developed()
    tiff16()
    sizes()
    damaged()
    describing()


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
    print("All raw-photo checks passed.")
