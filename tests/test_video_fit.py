"""A big video is brought down to fit a small machine -- and only when it has to be.

    python3 tests/test_video_fit.py

Measured on 2026-10-07 with ConvertBot's own ffmpeg arguments on a synthetic
3840x2160 clip, peak memory of ffmpeg:

    HDR -> mp4   1,334 MB     HDR -> webm   1,655 MB     (a 1 GB container: killed)
    SDR -> webm  1,182 MB     SDR -> mp4      887 MB

so CV-NO-MEMORY, which the owner's own 4K60 HDR phone clips produced. An HDR
video over FIT_PIXELS, and any video over it going to WEBM, is now scaled into
a 1920x1920 box before anything expensive happens (558 / 667 / 430 MB after).
This pins the rules, and runs the real ffmpeg on small clips to see the size
that comes out -- including the cases that must NOT change: a 4K MP4 that is
not HDR keeps its size, a clip already under the limit is untouched, and
nothing is ever made larger.

Review of 2026-10-07, added 2026-10-08: a video bigger than 4K (8K, 5.7K) is
brought down for every video target, HDR or not (it was never scaled, so it was
always killed for memory and refunded), the menu says so in its own note
("huge"), and a portrait phone video -- stored sideways with a rotation flag,
which ffprobe does not apply and ffmpeg does -- is reported the way up it is
shown, so the note does not give its size the wrong way round.

Needs ffmpeg with zscale (the HDR checks say so and skip without it).
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

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
import i18n  # noqa: E402

failures = []
WORK = pathlib.Path(tempfile.mkdtemp(prefix="videofit-"))


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL  ") + name + (f" -- {detail}" if not ok and detail else ""))
    if not ok:
        failures.append(name)


def rules():
    print("=== which videos are brought down ===")
    big = (3840, 2160)
    check("4K HDR to MP4 is brought down", cu.video_needs_fit(*big, True, "mp4"))
    check("4K HDR to MKV, MOV, AVI, WEBM too",
          all(cu.video_needs_fit(*big, True, t) for t in ("mkv", "mov", "avi", "webm")))
    check("4K SDR to WEBM is brought down", cu.video_needs_fit(*big, False, "webm"))
    check("4K SDR to MP4 keeps its size (it fits, narrowly)", not cu.video_needs_fit(*big, False, "mp4"))
    check("4K SDR to MKV and MOV keep theirs", not cu.video_needs_fit(*big, False, "mkv")
          and not cu.video_needs_fit(*big, False, "mov"))
    check("1080p HDR is untouched", not cu.video_needs_fit(1920, 1080, True, "mp4"))
    check("1080p to WEBM is untouched", not cu.video_needs_fit(1920, 1080, False, "webm"))
    check("2560x1080 ultrawide is untouched", not cu.video_needs_fit(2560, 1080, True, "mp4"))
    check("1440p HDR is brought down", cu.video_needs_fit(2560, 1440, True, "mp4"))
    check("a portrait 4K HDR phone video is brought down", cu.video_needs_fit(2160, 3840, True, "mp4"))
    check("audio, GIF and a still frame are not this rule's business",
          not any(cu.video_needs_fit(*big, True, t) for t in ("mp3", "gif", "webp", "apng", "png", "jpg")))
    check("an unreadable size is left alone", not cu.video_needs_fit(0, 0, True, "mp4"))
    saved = cu.FIT_PIXELS
    cu.FIT_PIXELS = 0
    check("CONVERT_VIDEO_FIT_PIXELS=0 turns it all off", not cu.video_needs_fit(*big, True, "webm"))
    check("... the 8K rule included", not cu.video_needs_fit(7680, 4320, False, "mp4"))
    cu.FIT_PIXELS = saved

    print("\n=== bigger than 4K is brought down, HDR or not ===")
    check("8K SDR to MP4, MKV, MOV, AVI and WEBM is brought down",
          all(cu.video_needs_fit(7680, 4320, False, t) for t in ("mp4", "mkv", "mov", "avi", "webm")))
    check("5.7K SDR (5760x3240) to MP4 is brought down", cu.video_needs_fit(5760, 3240, False, "mp4"))
    check("a portrait 8K phone video is brought down", cu.video_needs_fit(4320, 7680, False, "mp4"))
    check("8K HDR is brought down as it was", cu.video_needs_fit(7680, 4320, True, "mp4"))
    check("one pixel row over 4K is over", cu.video_needs_fit(3840, 2161, False, "mp4"))
    check("exactly 4K is not (it fits, narrowly)", not cu.video_needs_fit(3840, 2160, False, "mp4"))
    check("an 8K clip going to a still, a GIF or sound is not this rule's business",
          not any(cu.video_needs_fit(7680, 4320, False, t) for t in ("gif", "webp", "apng", "png", "jpg", "mp3")))
    check("the line is 4K's pixel count", cu.HUGE_PIXELS == 3840 * 2160)

    print("\n=== the note for a video bigger than 4K ===")
    for lang in ("en", "uz", "ru"):
        text = i18n.t(lang, "video_fit_huge_note", w=7680, h=4320, fw=1920, fh=1080)
        check(f"{lang}: video_fit_huge_note says both sizes and has no placeholder left",
              "7680" in text and "4320" in text and "1920" in text and "1080" in text and "{" not in text, text)
    exact = {
        "en": "⚠️ NOTE: this video is {w}×{h}. This server converts it at {fw}×{fh} — the full picture needs more memory than it has.",
        "uz": "⚠️ ESLATMA: bu video {w}×{h}. Server uni {fw}×{fh} o'lchamda o'giradi — to'liq tasvir uchun serverda xotira yetmaydi.",
        "ru": "⚠️ ВАЖНО: это видео {w}×{h}. Сервер конвертирует его в {fw}×{fh} — для полного кадра ему не хватает памяти."}
    check("the three texts are the ones the owner's notes asked for",
          all(i18n.STRINGS[lang]["video_fit_huge_note"] == text for lang, text in exact.items()))
    check("every video_fit key is in all three languages",
          all({k for k in i18n.STRINGS[lang] if k.startswith("video_fit_")}
              == {"video_fit_hdr_note", "video_fit_big_note", "video_fit_huge_note"} for lang in ("en", "uz", "ru")))

    print("\n=== which way a rotation turns the picture ===")
    turned = cu._quarter_turn
    check("a display matrix of 90 or -90 swaps the sides", turned({"side_data_list": [{"rotation": 90}]})
          and turned({"side_data_list": [{"rotation": -90}]}))
    check("and of 270, -270 or 89.99999 (ffprobe's rounding)", turned({"side_data_list": [{"rotation": 270}]})
          and turned({"side_data_list": [{"rotation": -270}]}) and turned({"side_data_list": [{"rotation": 89.99999}]}))
    check("0 and 180 do not", not turned({"side_data_list": [{"rotation": 0}]})
          and not turned({"side_data_list": [{"rotation": 180}]}) and not turned({"side_data_list": [{"rotation": -180}]}))
    check("the older rotate tag does the same", turned({"tags": {"rotate": "90"}}) and turned({"tags": {"rotate": "270"}})
          and not turned({"tags": {"rotate": "180"}}))
    check("a file with both is turned once, by the side data",
          turned({"side_data_list": [{"rotation": 90}], "tags": {"rotate": "90"}})
          and not turned({"side_data_list": [{"rotation": 180}], "tags": {"rotate": "90"}}))
    check("side data that is not a rotation (a mastering display, say) is skipped",
          turned({"side_data_list": [{"side_data_type": "Mastering display metadata"}, {"rotation": -90}]}))
    check("nothing, nonsense and NaN do not", not turned({}) and not turned({"tags": {"rotate": "N/A"}})
          and not turned({"side_data_list": [{"rotation": None}]}) and not turned({"side_data_list": [{"rotation": float("nan")}]})
          and not turned({"side_data_list": "oops"}) and not turned({"tags": None}))

    # video_geometry on what ffprobe says, without running it: the phone's portrait 4K clip, stored 3840x2160.
    def probed(stream):
        class Done:
            stdout = json.dumps({"streams": [stream]})
        real = (cu.shutil.which, cu.subprocess.run)
        cu.shutil.which = lambda name: "ffprobe"
        cu.subprocess.run = lambda *a, **k: Done()
        try:
            return cu.video_geometry("clip.mp4")
        finally:
            cu.shutil.which, cu.subprocess.run = real
    stored = {"width": 3840, "height": 2160, "color_transfer": "bt709"}
    check("a portrait 4K phone clip (stored 3840x2160, turned -90) is 2160x3840",
          probed(dict(stored, side_data_list=[{"side_data_type": "Display Matrix", "rotation": -90}])) == (2160, 3840, False))
    check("... and with the older tag (ffprobe 5 and before), the same",
          probed(dict(stored, tags={"rotate": "90"})) == (2160, 3840, False))
    check("... an unturned clip is as it was stored", probed(stored) == (3840, 2160, False))
    check("... and HDR is still read", probed(dict(stored, color_transfer="smpte2084", tags={"rotate": "270"})) == (2160, 3840, True))
    check("... a clip with no size is nothing", probed({"codec_name": "h264"}) is None)

    print("\n=== the size that comes out ===")
    check("3840x2160 -> 1920x1080", cu.fitted_size(3840, 2160) == (1920, 1080))
    check("portrait 2160x3840 -> 1080x1920", cu.fitted_size(2160, 3840) == (1080, 1920))
    check("4:3 4000x3000 -> 1920x1440", cu.fitted_size(4000, 3000) == (1920, 1440))
    check("nothing is scaled up", cu.fitted_size(1280, 720) == (1280, 720))
    # 1919 x 1001 is not scaled at all and is odd both ways; 3841 x 2161 scales to 1919.99 x 1080.2.
    check("always even numbers (an odd width breaks the encoder)", all(
        w % 2 == 0 and h % 2 == 0 for w, h in
        (cu.fitted_size(3841, 2161), cu.fitted_size(2161, 3841), cu.fitted_size(1919, 1001))),
        str([cu.fitted_size(3841, 2161), cu.fitted_size(2161, 3841)]))

    print("\n=== the filter is put in front of what was there ===")
    plain = ["-c:v", "libx264", "-crf", "23"]
    out = cu._fitted(plain)
    check("no filter yet: one is added, first", out[:2] == ["-vf", cu._FIT_FILTER] and out[2:] == plain)
    withvf = ["-vf", "fps=30", "-c:v", "libx264"]
    out = cu._fitted(withvf)
    check("an existing filter keeps its place after it", out[out.index("-vf") + 1] == cu._FIT_FILTER + ",fps=30")
    toned = cu._fitted(cu._tonemapped(["-c:v", "libx264"], "mp4"))
    chain = toned[toned.index("-vf") + 1]
    check("scale comes BEFORE the tonemap (the tonemap's cost is the picture's area)",
          chain.startswith("scale=") and chain.index("scale=") < chain.index("zscale"), chain[:80])


def turn(source, degrees, name):
    """The same video with a display-matrix rotation written next to it (not applied): how a phone stores a portrait clip."""
    path = WORK / name
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-hide_banner", "-loglevel", "error", "-display_rotation", str(degrees),
                    "-i", str(source), "-c", "copy", str(path)], check=True, timeout=60)
    return path


def big_and_turned():
    """Small clips standing in for the big ones: the thresholds are moved down so a 1280x720 clip is 'bigger than 4K'."""
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        print("\n(no ffmpeg here: the rotation checks are skipped)")
        return
    print("\n=== a portrait phone video, stored sideways ===")
    flat = make("flat.mp4", "1280x720", False)
    for degrees in (90, -90, 270):
        clip = turn(flat, degrees, f"turned{degrees}.mp4")
        got = cu.video_geometry(clip)
        check(f"stored 1280x720 with a rotation of {degrees} is shown 720x1280", got is not None and got[:2] == (720, 1280), str(got))
    clip = turn(flat, 180, "turned180.mp4")
    check("a rotation of 180 keeps the sides", cu.video_geometry(clip)[:2] == (1280, 720))
    check("no rotation keeps them", cu.video_geometry(flat)[:2] == (1280, 720))

    saved = (cu.FIT_PIXELS, cu.FIT_BOX, cu._FIT_FILTER, cu.HUGE_PIXELS)
    cu.FIT_PIXELS, cu.FIT_BOX, cu.HUGE_PIXELS = 100_000, 480, 10 ** 9
    cu._FIT_FILTER = cu._FIT_FILTER.replace("1920", "480")
    try:
        portrait = turn(flat, 90, "portrait.mp4")
        note = cu.video_fit_note(portrait)
        check("the note gives a portrait video's size the way up it is shown, and the size it will come out",
              note == ("big", 720, 1280, 270, 480), str(note))
        out = WORK / "portrait-fit.webm"
        cu.convert_media_ffmpeg(portrait, "webm", out)
        check("... and the fit box fits the turned picture: what comes out is the size the note promised",
              dims(out)[:2] == (270, 480), str(dims(out)))
        check("... which is not what the same clip would say stored the other way up",
              cu.video_fit_note(flat) == ("big", 1280, 720, 480, 270))

        print("\n=== bigger than 4K goes down for every video target ===")
        cu.HUGE_PIXELS = 1280 * 720 - 1
        huge = cu.video_fit_note(flat)
        check("an SDR clip over the line gets the 'huge' note, with both sizes", huge == ("huge", 1280, 720, 480, 270), str(huge))
        check("... whichever way up it is shown", cu.video_fit_note(portrait) == ("huge", 720, 1280, 270, 480))
        for target in ("mp4", "mkv", "mov"):
            out = WORK / f"huge.{target}"
            cu.convert_media_ffmpeg(flat, target, out)
            check(f"SDR over the line to {target.upper()} comes out 480x270", dims(out)[:2] == (480, 270), str(dims(out)))
        cu.HUGE_PIXELS = 1280 * 720
        out = WORK / "exactly.mp4"
        cu.convert_media_ffmpeg(flat, "mp4", out)
        check("exactly on the line it keeps its size", dims(out)[:2] == (1280, 720), str(dims(out)))
        cu.FIT_PIXELS = 0
        cu.HUGE_PIXELS = 1000
        out = WORK / "off.mp4"
        cu.convert_media_ffmpeg(flat, "mp4", out)
        check("with fitting off (CONVERT_VIDEO_FIT_PIXELS=0) nothing is brought down, whatever the size",
              dims(out)[:2] == (1280, 720) and cu.video_fit_note(flat) is None, str(dims(out)))
    finally:
        cu.FIT_PIXELS, cu.FIT_BOX, cu._FIT_FILTER, cu.HUGE_PIXELS = saved


def make(name, size, hdr, seconds=1, fps=5, vcodec="libx265"):
    path = WORK / name
    if hdr:
        cmd = ["ffmpeg", "-nostdin", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
               "-i", f"testsrc2=size={size}:rate={fps}", "-t", str(seconds), "-vf", "format=yuv420p10le",
               "-c:v", "libx265", "-preset", "ultrafast",
               "-x265-params", "colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:log-level=error",
               "-tag:v", "hvc1", "-color_primaries", "bt2020", "-color_trc", "smpte2084",
               "-colorspace", "bt2020nc", str(path)]
    else:
        cmd = ["ffmpeg", "-nostdin", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
               "-i", f"testsrc2=size={size}:rate={fps}", "-t", str(seconds), "-c:v", "libx264",
               "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True, timeout=240)
    return path


def dims(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,color_transfer", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, timeout=30).stdout.strip().split(",")
    return int(out[0]), int(out[1]), (out[2] if len(out) > 2 else "")


def real_runs():
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        print("\n(no ffmpeg here: the real conversions are skipped)")
        return
    print("\n=== real ffmpeg, small clips ===")
    hdr_ok = cu._ffmpeg_has_filters("zscale", "tonemap")
    sdr4k = make("sdr4k.mp4", "3840x2160", False)
    check("the probe reads a clip's size and that it is not HDR", cu.video_geometry(sdr4k) == (3840, 2160, False))
    out = WORK / "o1.webm"
    cu.convert_media_ffmpeg(sdr4k, "webm", out)
    check("4K SDR to WEBM comes out 1920x1080", dims(out)[:2] == (1920, 1080), str(dims(out)))
    out = WORK / "o2.mp4"
    cu.convert_media_ffmpeg(sdr4k, "mp4", out)
    check("4K SDR to MP4 keeps 3840x2160", dims(out)[:2] == (3840, 2160), str(dims(out)))
    small = make("small.mp4", "1280x720", False)
    out = WORK / "o3.webm"
    cu.convert_media_ffmpeg(small, "webm", out)
    check("720p to WEBM is not touched", dims(out)[:2] == (1280, 720), str(dims(out)))
    portrait = make("portrait.mp4", "2160x3840", False)
    out = WORK / "o4.webm"
    cu.convert_media_ffmpeg(portrait, "webm", out)
    check("a portrait 4K clip comes out 1080x1920", dims(out)[:2] == (1080, 1920), str(dims(out)))

    if not hdr_ok:
        print("  (this ffmpeg has no zscale: the HDR checks are skipped)")
        return
    hdr4k = make("hdr4k.mp4", "3840x2160", True)
    check("the probe sees HDR", cu.video_geometry(hdr4k) == (3840, 2160, True), str(cu.video_geometry(hdr4k)))
    out = WORK / "o5.mp4"
    cu.convert_media_ffmpeg(hdr4k, "mp4", out)
    w, h, trc = dims(out)
    check("4K HDR to MP4 comes out 1920x1080", (w, h) == (1920, 1080), str((w, h)))
    check("and is tonemapped to BT.709, not left on the HDR curve", trc == "bt709", trc)
    out = WORK / "o6.mkv"
    cu.convert_media_ffmpeg(hdr4k, "mkv", out)
    check("4K HDR to MKV comes out 1920x1080 too", dims(out)[:2] == (1920, 1080), str(dims(out)))
    hdr1080 = make("hdr1080.mp4", "1920x1080", True)
    out = WORK / "o7.mp4"
    cu.convert_media_ffmpeg(hdr1080, "mp4", out)
    check("1080p HDR keeps its size and is still tonemapped", dims(out) == (1920, 1080, "bt709"), str(dims(out)))
    saved = cu.FIT_PIXELS
    cu.FIT_PIXELS = 0
    out = WORK / "o8.webm"
    cu.convert_media_ffmpeg(sdr4k, "webm", out)
    cu.FIT_PIXELS = saved
    check("with the rule off a 4K clip keeps its size", dims(out)[:2] == (3840, 2160), str(dims(out)))

    print("\n=== what the person is told before they pay ===")
    note = cu.video_fit_note(hdr4k)
    check("a 4K HDR clip gets the HDR note, with both sizes", note == ("hdr", 3840, 2160, 1920, 1080), str(note))
    note = cu.video_fit_note(sdr4k)
    check("a 4K SDR clip gets the WEBM note", note == ("big", 3840, 2160, 1920, 1080), str(note))
    check("a small clip gets nothing", cu.video_fit_note(small) is None)
    check("a file that is not a video gets nothing", cu.video_fit_note(__file__) is None)


if __name__ == "__main__":
    # --rules: the pure checks only (seconds); --quick: those and the small clips;
    # nothing: and the 4K and HDR conversions too, which take minutes.
    rules()
    if "--rules" not in sys.argv:
        big_and_turned()
    if "--rules" not in sys.argv and "--quick" not in sys.argv:
        real_runs()
    shutil.rmtree(WORK, ignore_errors=True)
    print()
    if failures:
        print(f"FAILURES: {len(failures)}")
        for f in failures:
            print("  -", f)
        sys.exit(1)
    print("All video-fit checks passed.")
