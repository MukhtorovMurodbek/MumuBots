import math
import os
import shutil
import subprocess
import tempfile

import i18n
import sandbox

STICKER_SIDE = 512
MAX_DURATION_S = 3
MAX_FPS = 30
MAX_BYTES = 256 * 1024

_FIRST_CRF = 34
_MIN_CRF, _MAX_CRF = 20, 63
_MAX_ATTEMPTS = int(os.environ.get("STICKER_ENCODE_ATTEMPTS", "4"))

_TIMEOUT_S = int(os.environ.get("FFMPEG_TIMEOUT_SECONDS", "120"))

_THREADS = os.environ.get("FFMPEG_THREADS", "2")

def _scale_to(side: int) -> str:
    return f"scale=w='if(gte(iw,ih),{side},-2)':h='if(gte(iw,ih),-2,{side})'"


_SCALE_FILTER = _scale_to(STICKER_SIDE)


def _filters(speed: float, low_detail: bool) -> str:
    parts = []
    if speed and speed != 1:
        parts.append(f"setpts=PTS/{speed:g}")
    if low_detail:
        parts.append(_scale_to(LOW_DETAIL_SIDE))
    parts.append(_SCALE_FILTER)
    return ",".join(parts)


class ConversionError(Exception):
    pass


class TooBig(ConversionError):
    pass


MAX_SPEED = float(os.environ.get("STICKER_MAX_SPEED", "4"))
SPEED_PRESETS = (1.5, 2.0, 3.0, 4.0)
SPEED_CHOICES = 3
LOW_DETAIL_SIDE = 256
LOW_FPS = 15
_TOO_LONG_SLACK_S = 0.05


def clip_duration(input_bytes: bytes) -> "float | None":
    if not shutil.which("ffprobe") or not input_bytes:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "in.bin")
        with open(path, "wb") as f:
            f.write(input_bytes)
        for entries in ("format=duration", "stream=duration"):
            try:
                proc = subprocess.run(
                    sandbox.wrap(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", entries,
                                  "-of", "default=noprint_wrappers=1:nokey=1", path], [tmp]),
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
    tenths = math.ceil(round(duration / MAX_DURATION_S * 10, 6))
    return max(tenths, 11) / 10


def speed_choices(duration: "float | None") -> list[float]:
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
    overshoot = size / MAX_BYTES
    step = 6
    while overshoot > 2 and step < 24:
        overshoot /= 2
        step += 6
    target = crf + step
    if target <= _MAX_CRF:
        return target, fps
    return _MAX_CRF, 15 if fps <= 24 else 24


def _encode(in_path: str, out_path: str, crf: int, fps: int,
            vf: str = _SCALE_FILTER) -> subprocess.CompletedProcess:
    cmd = [
        "ffmpeg", "-nostdin", "-y",
        "-threads", _THREADS, "-filter_threads", _THREADS, "-filter_complex_threads", _THREADS,
        "-i", in_path,
        "-t", str(MAX_DURATION_S),
        "-an",
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
    folders = sorted({os.path.dirname(os.path.abspath(in_path)), os.path.dirname(os.path.abspath(out_path))})
    return subprocess.run(sandbox.wrap(cmd, folders), capture_output=True, text=True, timeout=_TIMEOUT_S)


def to_video_sticker_webm(input_bytes: bytes, lang: str = "en", speed: float = 1.0,
                          low_detail: bool = False, low_fps: bool = False) -> bytes:
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
                break

        raise TooBig(i18n.t(lang, "video_convert_too_big", note=last_note))
