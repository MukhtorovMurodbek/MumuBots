"""Convert arbitrary GIFs/videos (and already-valid Telegram video stickers,
re-encoded for safety) into Telegram-compliant video stickers.

Telegram's rule for video stickers: WEBM container, VP9 codec, no audio
track, one side exactly 512px (the other <=512px), <=3 seconds, <=30 FPS,
<=256 KB.

Hitting 256 KB is the hard part, and no single quality setting does it for
every clip. This used to walk a fixed ladder of eight increasingly
aggressive CRF values, re-encoding from scratch each time -- up to eight
full VP9 encodes of the same clip, which on a small shared vCPU is the
single most expensive thing this bot family does, and it happened most often
for exactly the busy clips that were slowest to encode.

Instead this now *measures*: the first attempt reports how far off it was,
and the next CRF is derived from that ratio rather than from a fixed step.
Two attempts cover almost everything, four is the ceiling, and the encoder
itself is capped to a couple of threads so one user's sticker cannot take
the whole container's CPU away from everyone else's messages.

A clip that cannot fit is no longer cut short or squeezed silently (since
2.1.6): one over MAX_DURATION_S is offered speeds that fit it (speed_choices),
and one that stays over MAX_BYTES raises TooBig, so the person can pick a lower
resolution or frame rate (bot.py: _offer_video_fit).

Requires ffmpeg (with libvpx-vp9) on PATH.
"""
import math
import os
import shutil
import subprocess
import tempfile

import i18n

STICKER_SIDE = 512
MAX_DURATION_S = 3
MAX_FPS = 30
MAX_BYTES = 256 * 1024

# VP9's CRF scale is 0 (best) to 63 (worst). 34 lands inside the limit for
# most ordinary clips; the loop below moves from there based on what the
# encode actually produced.
_FIRST_CRF = 34
_MIN_CRF, _MAX_CRF = 20, 63
_MAX_ATTEMPTS = int(os.environ.get("STICKER_ENCODE_ATTEMPTS", "4"))

# Wall-clock ceiling per ffmpeg run. Without one, a pathological input can
# leave a process pinning a core until the container is restarted -- and the
# user, meanwhile, sees nothing at all.
_TIMEOUT_S = int(os.environ.get("FFMPEG_TIMEOUT_SECONDS", "120"))

# libvpx-vp9 will happily use every core it can see. On a shared host that is
# the host's core count, not this container's share, so it buys nothing and
# costs everyone else their latency. Two threads plus row-based multi-
# threading is the sweet spot for a 512px clip.
_THREADS = os.environ.get("FFMPEG_THREADS", "2")

# Picks 512 for whichever side is larger and scales the other proportionally
# (-2 keeps it even, which libvpx-vp9 requires) -- computed by ffmpeg itself
# from the input's actual width/height, so no separate probing step is needed.
def _scale_to(side: int) -> str:
    return f"scale=w='if(gte(iw,ih),{side},-2)':h='if(gte(iw,ih),-2,{side})'"


_SCALE_FILTER = _scale_to(STICKER_SIDE)


def _filters(speed: float, low_detail: bool) -> str:
    """The -vf chain: faster playback first (setpts), then the detail of a
    LOW_DETAIL_SIDE picture if asked, then the 512 px a sticker must be."""
    parts = []
    if speed and speed != 1:
        parts.append(f"setpts=PTS/{speed:g}")
    if low_detail:
        parts.append(_scale_to(LOW_DETAIL_SIDE))
    parts.append(_SCALE_FILTER)
    return ",".join(parts)


class ConversionError(Exception):
    """Raised when ffmpeg is missing or the clip can't be made to fit."""


class TooBig(ConversionError):
    """Every attempt came out over MAX_BYTES: the person picks how to make it smaller."""


# A clip longer than MAX_DURATION_S is not cut any more: the person is offered
# speeds that fit it (or trims it themselves). The speed that fits exactly is
# rounded up to a tenth and never above MAX_SPEED -- a 57-second clip is not
# offered at 19x.
MAX_SPEED = float(os.environ.get("STICKER_MAX_SPEED", "4"))
SPEED_PRESETS = (1.5, 2.0, 3.0, 4.0)
SPEED_CHOICES = 3
# One side of a video sticker must be exactly 512 px, so "lower resolution"
# encodes the detail of a LOW_DETAIL_SIDE picture scaled back up to 512:
# softer, and far fewer bytes.
LOW_DETAIL_SIDE = 256
LOW_FPS = 15
_TOO_LONG_SLACK_S = 0.05


def clip_duration(input_bytes: bytes) -> "float | None":
    """How long the clip plays, in seconds, or None when ffprobe cannot tell."""
    if not shutil.which("ffprobe") or not input_bytes:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "in.bin")
        with open(path, "wb") as f:
            f.write(input_bytes)
        for entries in ("format=duration", "stream=duration"):
            try:
                proc = subprocess.run(
                    ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", entries,
                     "-of", "default=noprint_wrappers=1:nokey=1", path],
                    capture_output=True, text=True, timeout=30,
                )
            except (subprocess.TimeoutExpired, OSError):
                return None
            for line in proc.stdout.split():
                try:
                    value = float(line)
                except ValueError:
                    continue
                if value > 0:
                    return value
    return None


def is_too_long(duration: "float | None") -> bool:
    return bool(duration) and duration > MAX_DURATION_S + _TOO_LONG_SLACK_S


def fit_speed(duration: float) -> float:
    """The least speed-up, in tenths, that brings `duration` to MAX_DURATION_S or less."""
    tenths = math.ceil(round(duration / MAX_DURATION_S * 10, 6))
    return max(tenths, 11) / 10


def speed_choices(duration: "float | None") -> list[float]:
    """The speeds to offer for a clip this long: the one that fits exactly
    first, then the presets above it -- every one of them brings the clip to
    3 s or less, and none is over MAX_SPEED. Empty when nothing fits."""
    if not is_too_long(duration):
        return []
    exact = fit_speed(duration)
    if exact > MAX_SPEED:
        return []
    choices = [exact] + [p for p in SPEED_PRESETS if exact + 0.15 < p <= MAX_SPEED]
    return choices[:SPEED_CHOICES]


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _next_crf(crf: int, size: int, fps: int) -> tuple[int, int]:
    """Given an attempt that came out `size` bytes at `crf`, guess the CRF
    that lands under the limit -- and drop the frame rate once CRF alone has
    run out of room.

    VP9's bitrate falls off roughly geometrically in CRF: about 6 points of
    CRF halves it. So overshooting by 2x asks for +6, by 4x for +12, and a
    near miss only nudges. That is the whole trick -- the old ladder spent
    its attempts stepping past clips it had already measured."""
    overshoot = size / MAX_BYTES
    step = 6
    while overshoot > 2 and step < 24:
        overshoot /= 2
        step += 6
    target = crf + step
    if target <= _MAX_CRF:
        return target, fps
    # Out of quality headroom: keep the worst CRF and thin the frames out
    # instead. 24 then 15 fps, both well inside Telegram's "up to 30".
    return _MAX_CRF, 15 if fps <= 24 else 24


def _encode(in_path: str, out_path: str, crf: int, fps: int,
            vf: str = _SCALE_FILTER) -> subprocess.CompletedProcess:
    cmd = [
        "ffmpeg", "-nostdin", "-y",
        # Before -i, where it caps the decoder and the filters; the -threads
        # below the input caps the encoder alone. ffmpeg sizes each pool from
        # the host's core count -- 48 on Railway, whatever the container is
        # allowed -- and every thread holds frames: a 4K clip through this
        # command peaked at 528 MB with the cap on the encoder only, 1.3 GB as
        # a 48-core host would size it, and 172 MB with this. (ConvertBot found
        # the same on 2026-09-29; see convert_utils.FFMPEG_CAP_IN.)
        "-threads", _THREADS, "-filter_threads", _THREADS, "-filter_complex_threads", _THREADS,
        "-i", in_path,
        "-t", str(MAX_DURATION_S),
        "-an",  # video stickers must have no audio track
        "-r", str(fps),
        "-vf", vf,
        "-c:v", "libvpx-vp9",
        "-pix_fmt", "yuv420p",
        "-crf", str(crf),
        "-b:v", "0",
        "-deadline", "good",
        "-cpu-used", "5",
        "-row-mt", "1",
        "-threads", _THREADS,
        out_path,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=_TIMEOUT_S)


def to_video_sticker_webm(input_bytes: bytes, lang: str = "en", speed: float = 1.0,
                          low_detail: bool = False, low_fps: bool = False) -> bytes:
    """Takes raw bytes of a GIF, video, or video sticker and returns bytes
    of a compliant WEBM/VP9 video sticker. Raises ConversionError on failure,
    TooBig when every attempt stayed over MAX_BYTES.

    `speed` plays it faster (anything still over MAX_DURATION_S is cut);
    `low_detail` and `low_fps` are the person's choices for a clip that would
    not fit MAX_BYTES.

    Runs synchronously (shells out to ffmpeg) -- call via asyncio.to_thread
    from async code so it doesn't block the event loop. `lang` only affects
    the text of any ConversionError raised (this function itself has no
    other user-facing output).
    """
    if not _ffmpeg_available():
        raise ConversionError(i18n.t(lang, "video_convert_ffmpeg_missing"))
    if not input_bytes:
        raise ConversionError(i18n.t(lang, "video_convert_empty_file"))

    with tempfile.TemporaryDirectory() as tmp:
        in_path = os.path.join(tmp, "in.bin")
        with open(in_path, "wb") as f:
            f.write(input_bytes)

        crf, fps = _FIRST_CRF, (LOW_FPS if low_fps else MAX_FPS)
        vf = _filters(speed, low_detail)
        last_note = "unknown error"
        seen: set[tuple[int, int]] = set()

        for attempt in range(_MAX_ATTEMPTS):
            out_path = os.path.join(tmp, f"out_{attempt}.webm")
            try:
                proc = _encode(in_path, out_path, crf, fps, vf)
            except subprocess.TimeoutExpired:
                raise ConversionError(
                    i18n.t(lang, "video_convert_too_big", note=f"encoding timed out after {_TIMEOUT_S}s")
                ) from None

            if proc.returncode != 0:
                # A failed encode says nothing about size, so there is no
                # measurement to steer by -- stop rather than burn the
                # remaining attempts on the same error.
                raise ConversionError(
                    i18n.t(lang, "video_convert_too_big",
                           note=proc.stderr.strip()[-300:] or "ffmpeg failed with no output")
                )

            size = os.path.getsize(out_path)
            if size <= MAX_BYTES:
                with open(out_path, "rb") as f:
                    return f.read()

            last_note = f"still {size // 1024} KB at crf={crf}, {fps}fps"
            seen.add((crf, fps))
            crf, fps = _next_crf(crf, size, fps)
            if low_fps:
                fps = min(fps, LOW_FPS)
            if (crf, fps) in seen:
                break  # nowhere left to go; another identical encode is waste

        raise TooBig(i18n.t(lang, "video_convert_too_big", note=last_note))
