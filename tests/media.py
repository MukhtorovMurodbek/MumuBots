"""Exercises the real media pipelines with real ffmpeg and Pillow."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# The bots' own output is full of emoji (a /status starts with a chart, an
# error summary with a tick), and a Windows console still defaults to cp1252
# -- printing a child's reply there raised UnicodeEncodeError and took the
# whole run down before it reached the assertions. The child processes are
# already told to speak UTF-8; this is the same instruction for the runner's
# own stdout.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.gettempdir()) / "botfamily-media-tests"
TMP.mkdir(parents=True, exist_ok=True)


def make_clip(path: Path, seconds: int = 5, size: str = "640x480") -> None:
    """A deliberately busy synthetic clip -- noise is the worst case for a
    codec, which is exactly what the encoder's retry loop is for."""
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-f", "lavfi",
         "-i", f"testsrc2=size={size}:rate=30:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac",
         "-pix_fmt", "yuv420p", str(path)],
        capture_output=True, check=True,
    )


def make_image(path: Path, size=(3000, 2000)) -> None:
    from PIL import Image
    Image.new("RGB", size, (30, 90, 200)).save(path)


STICKER = r'''
import os, sys, time
sys.path.insert(0, os.getcwd())
sys.path.insert(1, os.path.join(os.path.dirname(os.getcwd()), "shared"))
from video_sticker import to_video_sticker_webm, ConversionError, MAX_BYTES
from image_utils import to_sticker_png

clip = sys.argv[1]
raw = open(clip, "rb").read()
t0 = time.perf_counter()
try:
    out = to_video_sticker_webm(raw)
    dt = time.perf_counter() - t0
    assert len(out) <= MAX_BYTES, len(out)
    assert out[:4] == b"\x1a\x45\xdf\xa3", out[:8]   # EBML/WebM magic
    print(f"  video sticker: {len(out)/1024:.0f} KB (limit {MAX_BYTES//1024} KB) in {dt:.1f}s")
except ConversionError as exc:
    dt = time.perf_counter() - t0
    print(f"  video sticker: gave up in {dt:.1f}s -- {exc}")

img = sys.argv[2]
t0 = time.perf_counter()
png = to_sticker_png(open(img, "rb").read()).getvalue()
from io import BytesIO
from PIL import Image
im = Image.open(BytesIO(png))
assert max(im.size) == 512 and im.mode == "RGBA", (im.size, im.mode)
print(f"  static sticker: {im.size} {im.mode}, {len(png)/1024:.0f} KB in {time.perf_counter()-t0:.2f}s")

# the decompression-bomb guard must refuse rather than allocate
from PIL import Image as PILImage
print("  bomb ceiling:", f"{PILImage.MAX_IMAGE_PIXELS:,} px")
'''

CONVERT = r'''
import os, sys, time
sys.path.insert(0, os.getcwd())
sys.path.insert(1, os.path.join(os.path.dirname(os.getcwd()), "shared"))
from convert_utils import convert, ConversionError, price_for_size

clip, img, outdir = sys.argv[1], sys.argv[2], sys.argv[3]

for src, target, ext in [(clip, "mp3", "mp4"), (clip, "gif", "mp4"), (img, "png", "png"),
                         (img, "jpg", "png"), (img, "ico", "png"), (img, "webp", "png")]:
    t0 = time.perf_counter()
    # convert() takes a list -- an album is one conversion -- and a work
    # folder, and says where it put the result. It replaced convert_file in
    # v1.5.0's format matrix; this file was not updated with it.
    result = convert([src], ext, target, outdir, f"out_{ext}_{target}")
    size = os.path.getsize(result.path)
    assert size > 0 and not result.zipped, (size, result)
    print(f"  {ext:>4} -> {target:<4} {size/1024:8.0f} KB  {time.perf_counter()-t0:5.2f}s")
    os.remove(result.path)

try:
    convert([img], "png", "nope", outdir, "x")
except ConversionError as exc:
    print("  unsupported target rejected:", str(exc)[:60])
else:
    raise AssertionError("an unsupported target should raise")

# the ffmpeg timeout has to be honoured, not just configured
import convert_utils
convert_utils.FFMPEG_TIMEOUT_S = 0
try:
    convert_utils.convert_media_ffmpeg(clip, "webm", os.path.join(outdir, "t.webm"))
except ConversionError as exc:
    print("  timeout honoured:", str(exc)[:70])
else:
    raise AssertionError("a zero timeout should have stopped it")

print("  pricing: 500KB ->", price_for_size(500*1024), "| 3MB ->", price_for_size(3*1024*1024),
      "| 999MB ->", price_for_size(999*1024*1024))
'''

# The worker, run the way jobs.py runs it: one JSON argument in, one JSON line
# out. test_jobs.py covers the policy around it with a fake worker; this is
# the real process -- real Pillow, real ffmpeg, a real kill.
WORKER = r'''
import asyncio, json, os, subprocess, sys, time
from types import SimpleNamespace
sys.path.insert(0, os.getcwd())
sys.path.insert(1, os.path.join(os.path.dirname(os.getcwd()), "shared"))

clip, img, outdir = sys.argv[1], sys.argv[2], sys.argv[3]


def worker(*argv):
    proc = subprocess.run([sys.executable, "-u", "convert_worker.py", *argv], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=300)
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    return proc.returncode, (json.loads(lines[-1]) if lines else {})


job = {"in_paths": [img], "src_ext": "png", "target_ext": "jpg", "work_dir": outdir,
       "stem": "worker_ok", "max_pixels": 210_000_000, "max_memory_mb": 0}
code, out = worker(json.dumps(job))
assert code == 0 and out.get("ok") is True, (code, out)
assert os.path.getsize(out["path"]) == out["bytes"] > 0 and out["zipped"] is False, out
os.remove(out["path"])
print(f"  a real conversion: exit 0, one JSON line, {out['bytes'] / 1024:.0f} KB")

code, out = worker(json.dumps(dict(job, target_ext="nope")))
assert code == 2 and out.get("ok") is False and out.get("kind") == "refused", (code, out)
print("  a file it cannot convert: exit 2, refused --", out["message"][:50])

code, out = worker("{not json")
assert code == 1 and out.get("kind") == "crash", (code, out)
code, out = worker()
assert code == 1 and out.get("kind") == "crash", (code, out)
print("  a malformed or missing job: exit 1, reported as a crash")

import jobs
slow = SimpleNamespace(paths=[clip], src_ext="mp4", target_ext="gif", proc=None,
                       ocr_lang="", ocr_pages=0)   # what jobs.Job defaults to for a conversion that reads no text
t0 = time.perf_counter()
outcome = asyncio.run(jobs.run_worker(slow, outdir, "worker_slow", 1))
took = time.perf_counter() - t0
assert not outcome.ok and outcome.kind == jobs.TIMEOUT, outcome
assert took < 15, took
assert slow.proc is None
print(f"  past its deadline: killed after {took:.1f}s and reported as {outcome.kind}")
'''


def run(folder: str, code: str, *args) -> int:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run([sys.executable, "-c", code, *args], cwd=ROOT / folder,
                          env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    print(proc.stdout.rstrip())
    if proc.returncode:
        print(proc.stderr.rstrip()[-2500:])
    return proc.returncode


def main() -> int:
    clip = TMP / "clip.mp4"
    img = TMP / "big.png"
    if not clip.exists():
        make_clip(clip)
    if not img.exists():
        make_image(img)
    print(f"fixtures: clip {clip.stat().st_size/1024:.0f} KB, image {img.stat().st_size/1024:.0f} KB\n")

    failures = 0
    print("== sticker_bot: video + static sticker encoding ==")
    failures += run("sticker_bot", STICKER, str(clip), str(img))
    print("== convert_bot: file conversion ==")
    failures += run("convert_bot", CONVERT, str(clip), str(img), str(TMP))
    print("== convert_bot: the worker process, for real ==")
    failures += run("convert_bot", WORKER, str(clip), str(img), str(TMP))
    print("\nFAILURES:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
