"""StickerBot's ffmpeg is capped on the decoder side as well as the encoder's.

    python3 tests/test_sticker_ffmpeg.py

ffmpeg sizes every thread pool from the host's core count -- 48 on Railway,
whatever the container is allowed -- and each thread holds frames. `-threads`
after the input caps the encoder and nothing else; the decoder and the filters
size their own pools, and take the host's count. Measured on a 4K clip through
this bot's own command: 528 MB with the cap on the encoder alone, 1.3 GB as a
48-core host sizes it (the container has 1 GB), and 172 MB with the cap before
`-i` as well. ConvertBot found the same on 2026-09-29 and caps all three; this
pins that StickerBot does too, in the shape test_conversions.py pins
ConvertBot's: the command itself is captured, and ffmpeg is never started.
"""
import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
MIRROR = HERE / ".mirror" / "sticker_bot"
sys.path.insert(0, str(MIRROR if MIRROR.exists() else HERE.parent / "sticker_bot"))
sys.path.insert(1, str(HERE.parent / "shared"))
sys.path.append(str(HERE / "stubs"))
os.environ.setdefault("SBOT_TOKEN", "test")
os.environ.setdefault("SBOT_USERNAME", "test")
os.environ.setdefault("DATABASE_URL", "postgresql://stub")
os.environ["ENV_FILES"] = "off"

import video_sticker                                    # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL  ") + name + (f" -- {detail}" if not ok and detail else ""))
    if not ok:
        failures.append(name)


def main() -> int:
    commands = []

    class Recorded(Exception):
        pass

    def record(cmd, *args, **kwargs):
        commands.append(list(cmd))
        raise Recorded()

    saved = (subprocess.run, video_sticker._ffmpeg_available)
    subprocess.run = record
    video_sticker._ffmpeg_available = lambda: True
    try:
        try:
            video_sticker._encode("in.bin", "out.webm", 34, 30)
        except Recorded:
            pass
    finally:
        subprocess.run, video_sticker._ffmpeg_available = saved

    check("ffmpeg was started once", len(commands) == 1, str(len(commands)))
    if commands:
        cmd = commands[0]
        split = cmd.index("-i")
        before, after = cmd[:split], cmd[split + 2:]
        check("the decoder is capped before -i", "-threads" in before, " ".join(before))
        check("the filters are capped before -i",
              "-filter_threads" in before and "-filter_complex_threads" in before, " ".join(before))
        check("the encoder is capped after -i", "-threads" in after, " ".join(after))
        caps = {cmd[i + 1] for i, word in enumerate(cmd)
                if word in ("-threads", "-filter_threads", "-filter_complex_threads")}
        check("all of them to the same number, from FFMPEG_THREADS",
              caps == {video_sticker._THREADS}, str(caps))

    print()
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for name in failures:
            print("  -", name)
        return 1
    print("StickerBot's ffmpeg is capped on both sides.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
