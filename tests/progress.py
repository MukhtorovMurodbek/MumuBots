"""ConvertBot's conversions report how far they have got -- with the real tools.

    python tests/progress.py

Needs ffmpeg (with ffprobe) on PATH, Pillow and PyMuPDF; no database, no
network. tests/test_card.py covers what the status card does with progress;
this covers where the progress comes from:

  ffmpeg, by its position against the file's duration;
  a document, page by page;
  an album, file by file;
  and all of it through the real worker process, into the job, the way the
  bot reads it while the person watches.
Nothing reports when nobody is listening, and a conversion is the same either way.

And one thing the real ffmpeg decides that nothing else can: an HDR10 video --
10-bit HEVC on the PQ curve, what the owner's phone records at 4K60 -- going
to MP4 is tonemapped to SDR and labelled BT.709, rather than copied across
grey and washed out; an SDR one is left alone; and for an animated target the
tonemap comes after the scale-down to 480 pixels, where it is cheap.
"""
import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
for variable in ("CBOT_API_ID", "CBOT_API_HASH", "LOCAL_BOT_API_URL"):
    os.environ.pop(variable, None)
sys.path.insert(0, str(ROOT / "convert_bot"))
sys.path.insert(1, str(ROOT / "shared"))

import convert_utils  # noqa: E402
import jobs  # noqa: E402

WORK = Path(tempfile.mkdtemp(prefix="convertbot-progress-"))
failures = []


def check(name, ok, detail=""):
    print(("  ok   " if ok else "  FAIL ") + name + ("" if ok else f"\n       {detail}"))
    if not ok:
        failures.append(name)


def listening():
    seen = []
    convert_utils.PROGRESS = lambda done, total: seen.append((done, total))
    return seen


def main() -> int:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("ffmpeg and ffprobe are needed on PATH")
        return 1

    print("== ffmpeg ==")
    video = WORK / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=4:size=320x240:rate=25",
                    "-pix_fmt", "yuv420p", str(video)], check=True)
    seen = listening()
    result = convert_utils.convert([video], "mp4", "webm", WORK, "clip-out")
    check("the conversion still produces its file", Path(result.path).is_file() and Path(result.path).stat().st_size)
    totals = {round(total, 1) for _, total in seen}
    check("it reports against the clip's duration", totals and all(3.9 <= t <= 4.1 for t in totals), totals)
    dones = [done for done, _ in seen]
    check("more than once, and never backwards", len(dones) >= 2 and dones == sorted(dones), dones[:12])
    check("reaching the end", dones and dones[-1] >= 3.5, dones[-3:])

    convert_utils.PROGRESS = None
    quiet = convert_utils.convert([video], "mp4", "mkv", WORK, "clip-quiet")
    check("with nobody listening it converts the plain way, and says nothing", Path(quiet.path).is_file())

    broken = WORK / "broken.mp4"
    broken.write_bytes(b"not a video at all" * 100)
    listening()
    try:
        convert_utils.convert([broken], "mp4", "webm", WORK, "broken-out")
        outcome = "converted"
    except convert_utils.ConversionError as exc:
        outcome = str(exc)
    check("a file ffmpeg cannot read still fails as a sentence, not a hang", outcome != "converted", outcome)

    print("\n== a document ==")
    import pymupdf
    pdf = WORK / "five.pdf"
    doc = pymupdf.open()
    for number in range(5):
        doc.new_page().insert_text((72, 72), f"page {number + 1}")
    doc.save(str(pdf))
    doc.close()
    seen = listening()
    result = convert_utils.convert([pdf], "pdf", "png", WORK, "pages")
    check("five pages come out", result.items == 5, result)
    check("reported page by page", seen[:5] == [(page, 5) for page in range(5)], seen)

    print("\n== an album ==")
    from PIL import Image
    pictures = []
    for number in range(3):
        path = WORK / f"picture{number}.png"
        Image.new("RGB", (40, 30), (number * 80, 90, 160)).save(path)
        pictures.append(path)
    seen = listening()
    result = convert_utils.convert(pictures, "png", "jpg", WORK, "album")
    check("three pictures, one zip", result.zipped and result.items == 3, result)
    check("reported file by file, ending at all three", [s for s in seen if s[1] == 3]
          == [(0, 3), (1, 3), (2, 3), (3, 3)], seen)
    convert_utils.PROGRESS = None

    print("\n== HDR ==")
    def stream(path):
        found = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                "stream=codec_name,pix_fmt,color_transfer", "-of", "default=nw=1", str(path)],
                               capture_output=True, text=True).stdout
        return dict(line.split("=", 1) for line in found.split() if "=" in line)

    hdr = WORK / "hdr10.mp4"
    made = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=duration=1:size=640x360:rate=30",
                           "-pix_fmt", "yuv420p10le", "-c:v", "libx265", "-preset", "ultrafast", "-x265-params",
                           "colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:hdr10=1:log-level=error",
                           "-color_primaries", "bt2020", "-color_trc", "smpte2084", "-colorspace", "bt2020nc", str(hdr)],
                          capture_output=True)
    if made.returncode or not convert_utils._ffmpeg_has_filters("zscale", "tonemap"):
        print("  (skipped: this ffmpeg has no libx265 to make an HDR clip, or no zscale/tonemap)")
    else:
        check("an HDR10 clip is recognised as HDR", convert_utils._is_hdr(hdr))
        check("and an SDR one is not", not convert_utils._is_hdr(video))
        out = convert_utils.convert([hdr], "mp4", "mp4", WORK, "hdr-out")
        info = stream(out.path)
        check("HDR10 to MP4 comes out 8-bit H.264 on the SDR curve, labelled as such",
              info.get("codec_name") == "h264" and info.get("pix_fmt") == "yuv420p"
              and info.get("color_transfer") == "bt709", info)
        sdr_out = convert_utils.convert([video], "mp4", "mkv", WORK, "sdr-out")
        check("an SDR video is not put through the tonemap", stream(sdr_out.path).get("color_transfer") != "bt709",
              stream(sdr_out.path))
        chain = convert_utils._tonemapped(convert_utils._FFMPEG_ARGS["gif"], "gif")
        vf = chain[chain.index("-vf") + 1]
        check("for a GIF the tonemap comes after the scale-down, not at 4K",
              vf.startswith("fps=15,scale=480:-1:flags=lanczos,zscale=t=linear"), vf[:80])
        gif = convert_utils.convert([hdr], "mp4", "gif", WORK, "hdr-gif")
        check("and the GIF is made", Path(gif.path).stat().st_size > 0)

    print("\n== through the worker, into the job ==")
    job = jobs.Job(user_id=1, chat_id=1, paths=[str(video)], src_ext="mp4", target_ext="webm",
                   size=video.stat().st_size, price=0, lang="en")
    outcome = asyncio.run(jobs.run_worker(job, WORK, "worker-out", 120))
    check("the worker converts", outcome.ok and outcome.bytes > 0, outcome)
    progress = job.extra.get("progress")
    check("and its progress reached the job as it went", progress and 3.9 <= progress[1] <= 4.1
          and progress[0] > 0, progress)

    shutil.rmtree(WORK, ignore_errors=True)
    print(f"\nFAILURES: {len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
