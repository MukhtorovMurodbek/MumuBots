"""A video arrives in its real shape, not as a square.

    python tests/test_video_shape.py

Telegram draws a video from the width, height and duration the sender passes; left
out, the clients show a 1:1 square and crop into it (an owner's friend saw a 9:16
reel as a square, 2026-10-12). DownloaderBot's _send_media now measures every file it
sends as a video (downloader_bot/video.py: probe, with the ffprobe that comes with
ffmpeg) and passes the three numbers. What is checked here:

  * shape_from_ffprobe, the pure half, on ffprobe's JSON: landscape and portrait;
    a quarter turn from the display matrix (90, -90, 270, -270, and 89.99999 which
    ffprobe's rounding produces) and from the older `rotate` tag, a half turn and
    none not swapping, the side data winning over the tag; a non-square pixel
    widening the picture before any turn; sizes that are missing, zero, negative,
    huge, text, float or boolean; a duration that is missing, N/A, NaN, infinite,
    negative or absurd;
  * probe, the real thing, on clips made with the ffmpeg on this machine (320x180,
    180x320, and one turned a quarter by a display matrix: made with ffmpeg's
    -display_rotation input option and -c copy, which writes the matrix without
    touching the picture): the numbers come back right; a text file, a missing file
    and a run with no ffprobe give None; the command is one argv with no shell, with
    nothing to read from and nothing to say, and the file named as `file:` so that
    what it contains cannot make it fetch anything; a fake ffprobe that never answers
    is killed and reaped within the time limit (and says so, for the album rule), one
    that says more than 64 KB or exits badly is ignored;
  * _send_media, the real one, with Telegram faked: the single video and every video
    of an album carry width, height and duration (the duration only when it is
    known), a video that could not be measured is sent as it always was, and
    documents (/lossless) and photos are never measured; after one ffprobe has timed
    out the rest of the album is not measured, and the next delivery is.

ffmpeg is needed for the real-clip checks (each says so and skips without). No
network, no database, nothing from Telegram.

To see a check catch breakage, copy the bot into tests/.mirror, break the copy (the
way tests/README.md describes) and run this again: it imports from the mirror when
there is one.
"""
import asyncio
import dataclasses
import logging
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
SRC = HERE / ".mirror" / "downloader_bot"
if not SRC.exists():
    SRC = REPO / "downloader_bot"
sys.path.insert(0, str(SRC))
sys.path.insert(1, str(REPO / "shared"))
sys.path.insert(0, str(HERE / "stubs"))

SCRATCH = pathlib.Path(tempfile.mkdtemp(prefix="downloaderbot-shape-"))
os.environ.update(ENV_FILES="off", DBOT_TOKEN="123:test", DBOT_USERNAME="test", DATABASE_URL="postgresql://stub",
                  DBOT_WORK_DIR=str(SCRATCH))

import bot             # noqa: E402
import resolvers       # noqa: E402
import video           # noqa: E402

logging.disable(logging.CRITICAL)

failures = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL  ") + name + (f" -- {detail}" if not ok and detail else ""))
    if not ok:
        failures.append(name)


def skip(name, why):
    print(f"  skip  {name} -- {why}")


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# shape_from_ffprobe
# ---------------------------------------------------------------------------

def js(w=320, h=180, sar="1:1", side=None, tag=None, duration="12.4", **extra):
    stream = {"width": w, "height": h, "tags": {}}
    if sar is not None:
        stream["sample_aspect_ratio"] = sar
    if side is not None:
        stream["side_data_list"] = [{"rotation": side}]
    if tag is not None:
        stream["tags"] = {"rotate": tag}
    stream.update(extra)
    data = {"streams": [stream]}
    if duration is not None:
        data["format"] = {"duration": duration}
    return data


S = video.shape_from_ffprobe
V = video.VideoShape


def pure_checks():
    print("\n== the shape from ffprobe's JSON ==")
    check("landscape: as stored, length rounded to whole seconds", S(js()) == V(320, 180, 12), S(js()))
    check("portrait 9:16 stays portrait", S(js(1080, 1920)) == V(1080, 1920, 12), S(js(1080, 1920)))
    check("a length of 12.6 is 13, and 0.4 is 0 (known, not missing)",
          S(js(duration="12.6")).duration == 13 and S(js(duration="0.4")).duration == 0)
    check("the length may be a number and not a string", S(js(duration=7.0)).duration == 7)

    print("\n== a quarter turn swaps the sides; nothing else does ==")
    for turn in (90, -90, 270, -270, 89.99999, -90.00001):
        check(f"display matrix {turn}: swapped", S(js(side=turn)) == V(180, 320, 12), S(js(side=turn)))
    for turn in (0, 180, -180, 360, 0.0, 45):
        check(f"display matrix {turn}: not swapped", S(js(side=turn)) == V(320, 180, 12), S(js(side=turn)))
    check("the older rotate tag, as ffprobe prints it (a string): 90 and 270 swap",
          S(js(tag="90")) == V(180, 320, 12) and S(js(tag="270")) == V(180, 320, 12))
    check("and 180 and 0 do not", S(js(tag="180")).width == 320 and S(js(tag="0")).width == 320)
    check("side data wins over the tag: a file with both is turned once",
          S(js(side=90, tag="90")).width == 180 and S(js(side=180, tag="90")).width == 320)
    check("side data that is not a rotation (a mastering display) is skipped",
          S({"streams": [{"width": 320, "height": 180, "side_data_list": [
              {"side_data_type": "Mastering display metadata"}, {"rotation": -90}]}]}).width == 180)
    for odd in ("N/A", "", "x", float("nan"), float("inf"), [], {}):
        check(f"a rotation of {odd!r} is none", S(js(side=odd)).width == 320 and S(js(tag=odd)).width == 320)
    check("side data that is not a list is ignored",
          S({"streams": [{"width": 320, "height": 180, "side_data_list": "oops", "tags": None}]}).width == 320)

    print("\n== a pixel that is not square widens the picture, before any turn ==")
    check("4:3 on 320x180 -> 427 wide", S(js(sar="4:3")) == V(427, 180, 12), S(js(sar="4:3")))
    check("1:1 and an equal pair (3:3) change nothing", S(js(sar="1:1")).width == 320 and S(js(sar="3:3")).width == 320)
    for odd in ("0:1", "1:0", "-4:3", "4:-3", "N/A", "0:0", "4/3", "a:b", "4:3:2", "1.5:1", ":", "4:", ""):
        check(f"a ratio of {odd!r} is ignored", S(js(sar=odd)).width == 320, S(js(sar=odd)))
    check("no ratio at all is fine", S(js(sar=None)).width == 320)
    check("a widened picture then turned: 720x480 at 32:27 is 853x480, and a quarter turn makes it 480x853",
          S(js(720, 480, sar="32:27", side=90)) == V(480, 853, 12), S(js(720, 480, sar="32:27", side=90)))
    check("a ratio that would make it wider than the limit is held at the limit",
          S(js(16384, 100, sar="9:1")).width == 16384 and S(js(100, 100, sar="1:9")).width == 11)
    check("a ratio of enormous numbers does not raise", S(js(sar="9" * 5000 + ":1")) is not None)

    print("\n== sizes that are not a picture ==")
    bad = [("no width", {"height": 180}), ("no height", {"width": 320}), ("width 0", {"width": 0, "height": 180}),
           ("height 0", {"width": 320, "height": 0}), ("negative", {"width": -320, "height": 180}),
           ("over 16384", {"width": 16385, "height": 180}), ("text", {"width": "320", "height": "180"}),
           ("float", {"width": 320.0, "height": 180.0}), ("boolean", {"width": True, "height": True}),
           ("null", {"width": None, "height": None})]
    for name, stream in bad:
        check(f"{name}: None", S({"streams": [stream], "format": {"duration": "1"}}) is None)
    check("16384 itself is fine", S({"streams": [{"width": 16384, "height": 1}]}) == V(16384, 1, None))
    for name, data in (("no streams", {"streams": []}), ("no streams key", {}), ("not a dict", None),
                       ("a list", []), ("a string", "x"), ("a stream that is not a dict", {"streams": [5]}),
                       ("streams that are not a list", {"streams": {"width": 3}})):
        check(f"{name}: None", S(data) is None)

    print("\n== the length ==")
    for name, d in (("missing", None), ("N/A", "N/A"), ("NaN", "nan"), ("infinite", "inf"), ("negative", "-1"),
                    ("empty", ""), ("absurd (a file can claim anything)", "9e18"), ("not a number", "soon"),
                    ("null", None)):
        shape = S(js(duration=d))
        check(f"{name}: the shape is still there, the length is None", shape == V(320, 180, None), shape)
    check("no format section at all is the same", S({"streams": [{"width": 4, "height": 3}]}) == V(4, 3, None))
    check("a 2-hour file is kept", S(js(duration="7200.2")).duration == 7200)
    check("the shape cannot be changed once made",
          _raises(dataclasses.FrozenInstanceError, lambda: setattr(S(js()), "width", 1)))
    check("the limits are what the spec says", video.PROBE_SECONDS == 5 and video.PROBE_MAX_BYTES == 64 * 1024
          and video.MAX_SIDE == 16384)


def _raises(error, call):
    try:
        call()
    except error:
        return True
    except Exception:
        return False
    return False


# ---------------------------------------------------------------------------
# probe, on real clips
# ---------------------------------------------------------------------------

def make_clip(path, size, extra_in=(), source=None, seconds=2):
    if source is None:
        cmd = ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "lavfi",
               "-i", f"testsrc2=size={size}:rate=15:duration={seconds}",
               "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)]
    else:
        cmd = ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", *extra_in, "-i", str(source), "-c", "copy", str(path)]
    return subprocess.run(cmd, capture_output=True).returncode == 0


FAKE = r'''
import sys, time
mode, counter = sys.argv[1], sys.argv[2]
with open(counter, "a") as f:
    f.write("run\n")
if mode.startswith("delay"):
    time.sleep(float(mode[5:]))
    mode = "good"
if mode == "slow":
    time.sleep(60)
elif mode == "big":
    sys.stdout.write("x" * 70000)
    sys.stdout.flush()
    time.sleep(60)
elif mode == "fail":
    sys.exit(3)
elif mode == "junk":
    sys.stdout.write("not json at all")
elif mode == "good":
    sys.stdout.write('{"streams": [{"width": 100, "height": 50, "sample_aspect_ratio": "1:1"}], "format": {"duration": "3.2"}}')
'''


def counted(counter):
    try:
        return len(pathlib.Path(counter).read_text().split())
    except OSError:
        return 0


class Fake:
    """Stands in for ffprobe: a Python script that does what `mode` says."""

    def __init__(self, mode):
        self.script = SCRATCH / "fake_ffprobe.py"
        self.script.write_text(FAKE)
        self.counter = SCRATCH / f"fake-{mode}-{time.time_ns()}.count"
        self.mode, self.real = mode, None

    def __enter__(self):
        self.real = video._ffprobe_argv
        video._ffprobe_argv = lambda: [sys.executable, str(self.script), self.mode, str(self.counter)]
        return self

    def __exit__(self, *exc):
        video._ffprobe_argv = self.real

    @property
    def runs(self):
        return counted(self.counter)


class Spy:
    """Records every subprocess probe starts, and passes them through."""

    def __init__(self):
        self.calls, self.processes = [], []

    def __enter__(self):
        self.real = asyncio.create_subprocess_exec

        async def spy(*args, **kwargs):
            self.calls.append((args, kwargs))
            process = await self.real(*args, **kwargs)
            self.processes.append(process)
            return process

        asyncio.create_subprocess_exec = spy
        return self

    def __exit__(self, *exc):
        asyncio.create_subprocess_exec = self.real


async def timed(coro):
    start = time.monotonic()
    result = await coro
    return result, time.monotonic() - start


def probe_checks():
    print("\n== probe, on real clips ==")
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        skip("probe on real clips", "ffmpeg and ffprobe are not on PATH")
        return
    land, port, turned = SCRATCH / "land.mp4", SCRATCH / "port.mp4", SCRATCH / "turned.mp4"
    check("made a 320x180 and a 180x320 clip", make_clip(land, "320x180") and make_clip(port, "180x320"))
    got = run(video.probe(str(land)))
    check("320x180, 2 seconds", got == V(320, 180, 2), got)
    got = run(video.probe(str(port)))
    check("180x320 stays portrait (this is the reel that was drawn as a square)", got == V(180, 320, 2), got)
    if make_clip(turned, None, extra_in=("-display_rotation", "90"), source=land):
        got = run(video.probe(str(turned)))
        check("the 320x180 clip turned a quarter by a display matrix is shown 180x320",
              got == V(180, 320, 2), got)
    else:
        skip("a turned clip", "this ffmpeg has no -display_rotation")

    text = SCRATCH / "not-a-video.txt"
    text.write_text("hello")
    check("a text file: None", run(video.probe(str(text))) is None)
    check("a missing file: None", run(video.probe(str(SCRATCH / "nope.mp4"))) is None)
    check("an empty file: None", run(video.probe(str(_touch(SCRATCH / "empty.mp4")))) is None)
    shutil.copy(land, land.with_name("-v.mp4"))
    _chdir(SCRATCH)
    got = run(video.probe("-v.mp4"))
    _chdir(HERE)
    check("a path that starts with a dash is a file, not an option", got == V(320, 180, 2), got)

    real = video._ffprobe_argv
    video._ffprobe_argv = lambda: None
    try:
        check("no ffprobe: None, and nothing started", run(_probe_with_spy(land)) == (None, 0))
    finally:
        video._ffprobe_argv = real
    which = shutil.which
    shutil.which = lambda name: None
    try:
        check("_ffprobe_argv looks on PATH and says None when it is not there", video._ffprobe_argv() is None)
    finally:
        shutil.which = which
    check("and finds it when it is", (video._ffprobe_argv() or [""])[0].lower().replace(".exe", "").endswith("ffprobe"))

    with Spy() as spy:
        run(video.probe(str(land)))
    args, kwargs = spy.calls[0] if spy.calls else ((), {})
    check("it starts ffprobe itself, as an argument list and not a shell string",
          len(spy.calls) == 1 and all(isinstance(a, str) for a in args) and "ffprobe" in args[0].lower())
    check("with nothing to read from and nothing to say",
          kwargs.get("stdin") == asyncio.subprocess.DEVNULL and kwargs.get("stderr") == asyncio.subprocess.DEVNULL,
          kwargs)
    check("asking for the first video stream's size, ratio, turn and the file's length, as JSON",
          "-select_streams" in args and "v:0" in args and args[-3:-1] == ("-of", "json")
          and any("width,height,sample_aspect_ratio" in a and "format=duration" in a and "stream_side_data=rotation" in a
                  and "stream_tags=rotate" in a for a in args), args)
    check("naming the file as file: and allowing only that protocol, so a 'video' that is really a playlist "
          "cannot make this server fetch anything",
          args[-1] == "file:" + str(land) and "-protocol_whitelist" in args
          and args[args.index("-protocol_whitelist") + 1] == "file", args)
    check("and the process was waited for", all(p.returncode is not None for p in spy.processes))


def _touch(path):
    path.write_bytes(b"")
    return path


def _chdir(path):
    os.chdir(path)
    return True


async def _probe_with_spy(path):
    with Spy() as spy:
        return await video.probe(str(path)), len(spy.calls)


def fake_checks():
    print("\n== a fake ffprobe: slow, loud, broken ==")
    real_seconds = video.PROBE_SECONDS
    video.PROBE_SECONDS = 1
    try:
        probe_run = video.ProbeRun()
        with Fake("slow") as fake, Spy() as spy:
            got, took = run(timed(video.probe("anything.mp4", probe_run)))
        check("one that never answers: None, in about the limit", got is None and 0.8 < took < 3.5, (got, took))
        check("the run is marked timed out, for the album rule", probe_run.timed_out is True)
        check("the process was killed and its exit collected (nothing left running or unreaped)",
              len(spy.processes) == 1 and spy.processes[0].returncode is not None, [p.returncode for p in spy.processes])
        check("it was started once", fake.runs == 1, fake.runs)

        video.PROBE_SECONDS = 4          # a Python start-up on a busy machine must not look like a stall
        probe_run = video.ProbeRun()
        with Fake("big") as fake, Spy() as spy:
            got, took = run(timed(video.probe("anything.mp4", probe_run)))
        check("one that says more than 64 KB: None, at once, and killed",
              got is None and took < 3.5 and spy.processes and spy.processes[0].returncode is not None, (got, took))
        check("that is not a timeout", probe_run.timed_out is False)

        for mode, name in (("fail", "one that exits with an error"), ("junk", "one that prints what is not JSON")):
            probe_run = video.ProbeRun()
            with Fake(mode):
                got = run(video.probe("anything.mp4", probe_run))
            check(f"{name}: None, not a timeout", got is None and probe_run.timed_out is False, got)
        with Fake("good") as fake:
            got = run(video.probe("anything.mp4"))
        check("and one that answers is believed", got == V(100, 50, 3), got)

        video.PROBE_SECONDS = 1

        async def cancelled():
            task = asyncio.ensure_future(video.probe("anything.mp4"))
            await asyncio.sleep(0.4)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                return True
            return False

        with Fake("slow"), Spy() as spy:
            was_cancelled = run(cancelled())
        check("a probe that is cancelled (the person gave up) kills and reaps its ffprobe too",
              was_cancelled and spy.processes and spy.processes[0].returncode is not None,
              [p.returncode for p in spy.processes])
    finally:
        video.PROBE_SECONDS = real_seconds


# ---------------------------------------------------------------------------
# _send_media
# ---------------------------------------------------------------------------

class Message:
    def __init__(self):
        self.sent = []

    async def reply_video(self, f, **kwargs):
        self.sent.append(("video", f.name, kwargs))

    async def reply_photo(self, f, **kwargs):
        self.sent.append(("photo", f.name, kwargs))

    async def reply_document(self, f, **kwargs):
        self.sent.append(("document", f.name, kwargs))

    async def reply_media_group(self, group, **kwargs):
        self.sent.append(("group", [(type(m).__name__, m.kwargs) for m in group], kwargs))

    async def reply_text(self, text, **kwargs):
        self.sent.append(("text", text, kwargs))


bot.get_caption_enabled = lambda user_id: False
bot.get_lossless_enabled = lambda user_id: False
USER = 7


def world(kinds, names=None):
    """Files on disk and the (item, path) list for them."""
    pairs = []
    for n, kind in enumerate(kinds):
        ext = "mp4" if kind == "video" else "jpg"
        path = SCRATCH / f"item{n}.{ext}"
        path.write_bytes(b"x" * 10)
        pairs.append((resolvers.MediaItem(kind, url=f"https://cdn.example/{n}", filename=f"item{n}.{ext}"), str(path)))
    return pairs


async def send(pairs, lossless=False):
    message = Message()
    update = SimpleNamespace(effective_user=SimpleNamespace(id=USER), effective_message=message)
    context = SimpleNamespace(user_data={})
    await bot._send_media(update, context, pairs, "en", lossless=lossless)
    return message.sent


SHAPES = {}
PROBED = []


async def fake_probe(path, run=None):
    PROBED.append(os.path.basename(path))
    return SHAPES.get(os.path.basename(path))


def send_checks():
    print("\n== _send_media passes the shape ==")
    real_probe = video.probe
    video.probe = fake_probe
    try:
        SHAPES.clear()
        SHAPES["item0.mp4"] = V(1080, 1920, 15)
        PROBED.clear()
        sent = run(send(world(["video"])))
        check("a single video: reply_video with its width, height and duration",
              len(sent) == 1 and sent[0][0] == "video"
              and (sent[0][2].get("width"), sent[0][2].get("height"), sent[0][2].get("duration")) == (1080, 1920, 15),
              sent)
        check("and still without a streaming flag or a thumbnail",
              not {"supports_streaming", "thumbnail", "cover"} & set(sent[0][2]), sent[0][2])

        SHAPES["item0.mp4"] = V(640, 360, None)
        sent = run(send(world(["video"])))
        check("a length that is not known is not passed (width and height still are)",
              "duration" not in sent[0][2] and sent[0][2].get("width") == 640 and sent[0][2].get("height") == 360,
              sent[0][2])
        SHAPES.clear()
        sent = run(send(world(["video"])))
        check("a video that could not be measured goes exactly as it did before: no shape at all",
              sent[0][0] == "video" and not {"width", "height", "duration"} & set(sent[0][2]), sent[0][2])

        print("\n== an album ==")
        SHAPES.update({"item1.mp4": V(720, 1280, 9), "item3.mp4": V(1280, 720, None)})
        PROBED.clear()
        sent = run(send(world(["photo", "video", "photo", "video", "video"])))
        group = sent[0][1] if sent and sent[0][0] == "group" else []
        kinds = [name for name, _ in group]
        check("photos and videos stay what they were", kinds == ["InputMediaPhoto", "InputMediaVideo", "InputMediaPhoto",
                                                                 "InputMediaVideo", "InputMediaVideo"], kinds)
        videos = [kw for name, kw in group if name == "InputMediaVideo"]
        check("every InputMediaVideo carries what was measured",
              videos[0].get("width") == 720 and videos[0].get("height") == 1280 and videos[0].get("duration") == 9
              and videos[1].get("width") == 1280 and "duration" not in videos[1]
              and not {"width", "height", "duration"} & set(videos[2]), videos)
        photos = [kw for name, kw in group if name == "InputMediaPhoto"]
        check("no photo is given a shape", all(not {"width", "height", "duration"} & set(kw) for kw in photos), photos)
        check("only the videos were measured", sorted(PROBED) == ["item1.mp4", "item3.mp4", "item4.mp4"], PROBED)

        print("\n== documents and photos are never measured ==")
        SHAPES["item0.mp4"] = V(1080, 1920, 15)
        PROBED.clear()
        sent = run(send(world(["video"]), lossless=True))
        check("a video sent as a file (/lossless): no shape, and ffprobe not asked",
              sent[0][0] == "document" and not {"width", "height", "duration"} & set(sent[0][2]) and PROBED == [], (sent, PROBED))
        sent = run(send(world(["video", "video"]), lossless=True))
        docs = sent[0][1] if sent and sent[0][0] == "group" else []
        check("an album sent as files: InputMediaDocument, no shape, not measured",
              [n for n, _ in docs] == ["InputMediaDocument"] * 2
              and all(not {"width", "height", "duration"} & set(kw) for _, kw in docs) and PROBED == [], (docs, PROBED))
        sent = run(send(world(["photo"])))
        check("a single photo: no shape, not measured",
              sent[0][0] == "photo" and not {"width", "height", "duration"} & set(sent[0][2]) and PROBED == [], (sent, PROBED))
    finally:
        video.probe = real_probe


def timeout_checks():
    print("\n== after one ffprobe has timed out, the rest of the album is not measured ==")
    if not hasattr(asyncio, "create_subprocess_exec"):
        skip("the timeout rule", "no subprocess support")
        return
    real_seconds = video.PROBE_SECONDS
    video.PROBE_SECONDS = 1
    try:
        pairs = world(["video", "video", "video", "photo", "video"])
        with Fake("slow") as fake:
            sent, took = run(timed(send(pairs)))
        group = sent[0][1] if sent and sent[0][0] == "group" else []
        check("a hostile album costs one wait, not one per video: ffprobe started once, in about a second",
              fake.runs == 1 and took < 3.5, (fake.runs, took))
        check("and every video went without a shape, as before",
              len(group) == 5 and all(not {"width", "height", "duration"} & set(kw) for _, kw in group), group)

        with Fake("slow") as fake:
            run(send(world(["video"])))
            run(send(world(["video"])))
        check("the next delivery measures again (the skip is for one delivery)", fake.runs == 2, fake.runs)

        video.PROBE_SECONDS = 4
        with Fake("good") as fake:
            sent = run(send(world(["video", "video", "video"])))
        group = sent[0][1] if sent and sent[0][0] == "group" else []
        check("with a working ffprobe all three are measured and sent with the shape",
              fake.runs == 3 and all(kw.get("width") == 100 and kw.get("height") == 50 and kw.get("duration") == 3
                                     for _, kw in group), (fake.runs, group))

        with Fake("fail") as fake:
            sent = run(send(world(["video", "video", "video"])))
        check("an ffprobe that merely fails is not a timeout: every video is tried", fake.runs == 3, fake.runs)
    finally:
        video.PROBE_SECONDS = real_seconds


def budget_checks():
    print("\n== one delivery gets ten seconds of probing in all ==")
    check("a probe may take five seconds and a whole delivery ten", video.PROBE_SECONDS == 5 and video.PROBE_BUDGET_SECONDS == 10)
    fresh_run = video.ProbeRun()
    check("a new run is not exhausted, and a probe may use its full limit",
          fresh_run.exhausted is False and fresh_run.allowance() == video.PROBE_SECONDS)
    fresh_run.spent = video.PROBE_BUDGET_SECONDS - 3
    check("with 3 s of the budget left a probe may take 3 s", fresh_run.allowance() == 3, fresh_run.allowance())
    fresh_run.spent = video.PROBE_BUDGET_SECONDS
    check("with none left the run is exhausted and a probe may take no time",
          fresh_run.exhausted is True and fresh_run.allowance() == 0)
    stalled = video.ProbeRun()
    stalled.timed_out = True
    check("a run that had a probe killed is exhausted too", stalled.exhausted is True)

    real = (video.PROBE_SECONDS, video.PROBE_BUDGET_SECONDS)
    try:
        video.PROBE_SECONDS, video.PROBE_BUDGET_SECONDS = 4, 1.6
        probe_run = video.ProbeRun()
        with Fake("delay0.5") as fake:
            for _ in range(5):
                run(video.probe("anything.mp4", probe_run))
        check("probes that are slow but not stalled add up: the budget stops the run before the fifth (and the "
              "ffprobe that crossed it was cut off, not waited for)",
              1 <= fake.runs <= 4 and probe_run.exhausted is True, fake.runs)
        check("what was spent is counted, and is about the budget and no more than a probe's own limit past it",
              video.PROBE_BUDGET_SECONDS - 0.2 <= probe_run.spent <= video.PROBE_BUDGET_SECONDS + 0.8, probe_run.spent)

        video.PROBE_SECONDS, video.PROBE_BUDGET_SECONDS = 4, 10
        probe_run = video.ProbeRun()
        probe_run.spent = 9.4
        with Fake("slow") as fake:
            got, took = run(timed(video.probe("anything.mp4", probe_run)))
        check("with 0.6 s of the budget left a stalled ffprobe is cut off then, not after its own 4 s",
              got is None and took < 3.0 and probe_run.timed_out is True, (got, took))

        probe_run = video.ProbeRun()
        probe_run.spent = video.PROBE_BUDGET_SECONDS
        with Fake("good") as fake, Spy() as spy:
            got = run(video.probe("anything.mp4", probe_run))
        check("a run whose budget is spent starts no ffprobe at all",
              got is None and fake.runs == 0 and spy.calls == [], (got, fake.runs, len(spy.calls)))
        with Fake("good") as fake:
            got = run(video.probe("anything.mp4"))
        check("a probe with no run is limited by its own five seconds only", got == V(100, 50, 3) and fake.runs == 1, got)
    finally:
        video.PROBE_SECONDS, video.PROBE_BUDGET_SECONDS = real


def large_checks():
    print("\n== a large download's items share one budget ==")
    calls = []

    async def record(update, context, items, lang, lossless=None, redeliver_token=None, probing=None):
        calls.append(probing)

    real_send = bot._send_media
    bot._send_media = record
    try:
        update = SimpleNamespace(effective_user=SimpleNamespace(id=USER), effective_message=Message())
        context = SimpleNamespace(user_data={})
        run(bot._send_large(update, context, world(["video", "video", "video"]), "en"))
        check("_send_large hands the same budget to each item it sends through _send_media",
              len(calls) == 3 and isinstance(calls[0], video.ProbeRun) and all(c is calls[0] for c in calls), calls)
        first = calls[0]
        calls.clear()
        run(bot._send_large(update, context, world(["video", "video"]), "en"))
        check("and the next delivery gets a budget of its own", len(calls) == 2 and calls[0] is not first
              and calls[0] is calls[1])
    finally:
        bot._send_media = real_send

    real_probe = video.probe
    probed = []

    async def costly_probe(path, run=None):
        probed.append(os.path.basename(path))
        run.spent += 6                       # each one takes six seconds (a slow disk)
        return V(640, 360, 5)

    video.probe = costly_probe
    try:
        sent = run(send_large(world(["video", "video", "video"])))
        shapes = [kw for kind, name, kw in sent if kind == "video"]
        check("three videos at six seconds each: the first two are measured, the third is sent as it always was",
              probed == ["item0.mp4", "item1.mp4", "item2.mp4"][:2] and len(shapes) == 3
              and shapes[0].get("width") == 640 and shapes[1].get("width") == 640 and "width" not in shapes[2],
              (probed, shapes))
    finally:
        video.probe = real_probe


async def send_large(pairs):
    message = Message()
    update = SimpleNamespace(effective_user=SimpleNamespace(id=USER), effective_message=message)
    context = SimpleNamespace(user_data={})
    await bot._send_large(update, context, pairs, "en")
    return message.sent


pure_checks()
probe_checks()
fake_checks()
send_checks()
timeout_checks()
budget_checks()
large_checks()

shutil.rmtree(SCRATCH, ignore_errors=True)
print()
if failures:
    print(f"FAILED ({len(failures)}):")
    for name in failures:
        print("  -", name)
    sys.exit(1)
print("A video arrives in its real shape.")
