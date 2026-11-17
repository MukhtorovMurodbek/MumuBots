import asyncio
import contextlib
import copy
import glob
import json
import logging
import math
import os
import re
import shutil
import sys
import tempfile
import threading
import time

import platforms
import sandbox
import uuid
from dataclasses import dataclass

MAX_DOWNLOAD_MB = int(os.environ.get("DBOT_MAX_DOWNLOAD_MB", "48"))

MAX_DURATION_S = int(os.environ.get("DBOT_MAX_DURATION_SECONDS", "10800"))

MAX_HEIGHT = int(os.environ.get("DBOT_MAX_HEIGHT") or 1080)
FRAGMENTS_AT_ONCE = max(1, int(os.environ.get("DBOT_YTDLP_FRAGMENTS") or 4))
SIZE_MARGIN = 0.96

SOCKET_TIMEOUT_S = int(os.environ.get("DBOT_SOCKET_TIMEOUT", "30"))

logger = logging.getLogger(__name__)

YT_PLAYER_CLIENTS = [
    c.strip() for c in (os.environ.get("DBOT_YT_PLAYER_CLIENTS") or "").split(",") if c.strip()
]

COOKIEFILE = os.environ.get("DBOT_COOKIES_FILE") or None
SITE_COOKIEFILES = {
    platforms.UNADVERTISED: os.environ.get("DBOT_YT_COOKIES_FILE") or None,
    "instagram": os.environ.get("DBOT_IG_COOKIES_FILE") or None,
    "tiktok": os.environ.get("DBOT_TT_COOKIES_FILE") or None,
    "twitter": os.environ.get("DBOT_TW_COOKIES_FILE") or None,
    "reddit": os.environ.get("DBOT_RD_COOKIES_FILE") or None,
}

_COOKIE_TEXT_VARS = {platforms.UNADVERTISED: "DBOT_YT_COOKIES", "instagram": "DBOT_IG_COOKIES",
                     "tiktok": "DBOT_TT_COOKIES", "twitter": "DBOT_TW_COOKIES",
                     "reddit": "DBOT_RD_COOKIES", "other": "DBOT_COOKIES"}
_cookie_text_files: dict = {}
_cookie_text_refused: dict = {}


def _cookie_text_problem(text: str) -> "str | None":
    text = text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    for number, line in enumerate(lines, 1):
        if not line.strip() or (line.strip().startswith("#") and not line.startswith("#HttpOnly_")):
            continue
        line = line.removeprefix("#HttpOnly_")
        fields = line.split("\t")
        if len(fields) != 7:
            return (f"line {number} of {len(lines)} has {len(fields)} tab-separated field(s), not 7 "
                    "(were the tabs pasted as spaces?)")
        domain, flag, path, secure, expires, name, _value = fields
        if not domain:
            what = "an empty domain"
        elif flag != ("TRUE" if domain.startswith(".") else "FALSE"):
            what = "a subdomains flag (field 2) that does not match its domain"
        elif not path.startswith("/"):
            what = "a path (field 3) that does not start with /"
        elif secure not in ("TRUE", "FALSE"):
            what = "a secure flag (field 4) that is not TRUE or FALSE"
        elif not re.fullmatch(r"[0-9]*", expires):
            what = "an expiry (field 5) that is not digits"
        elif not name:
            what = "an empty name (field 6)"
        else:
            continue
        return f"line {number} of {len(lines)} has {what}"
    return None


_COOKIE_HEADER_RE = re.compile(r"#( Netscape)? HTTP Cookie File")


def _cookiefile_from_text(site: str) -> "str | None":
    var = _COOKIE_TEXT_VARS.get(site)
    text = os.environ.get(var) if var else None
    if not text or not text.strip():
        return None
    text = text.removeprefix("\ufeff")
    if not text.strip():
        return None
    path = _cookie_text_files.get(site)
    if path and os.path.exists(path):
        return path
    if _cookie_text_refused.get(site) == os.environ.get(var):
        return None
    if "\\n" in text and "\n" not in text:
        text = text.replace("\\n", "\n").replace("\\t", "\t")
    problem = _cookie_text_problem(text)
    if problem:
        _cookie_text_refused[site] = os.environ.get(var)
        logger.warning("%s is not a cookies.txt: %s. Those cookies are not used.", var, problem)
        return None
    text = text.lstrip("\r\n \t")
    if not _COOKIE_HEADER_RE.match(text):
        text = "# Netscape HTTP Cookie File\n" + text
    fd, path = tempfile.mkstemp(prefix=f"cookies-{site}-", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text if text.endswith("\n") else text + "\n")
    _cookie_text_files[site] = path
    return path


def cookiefile_for(site: str) -> "str | None":
    for candidate in (SITE_COOKIEFILES.get(site), _cookiefile_from_text(site),
                      COOKIEFILE, _cookiefile_from_text("other")):
        if candidate and os.path.exists(candidate):
            return candidate
    return None


PROXY = os.environ.get("DBOT_PROXY") or None
SITE_PROXIES = {
    platforms.UNADVERTISED: os.environ.get("DBOT_YT_PROXY") or None,
    "instagram": os.environ.get("DBOT_IG_PROXY") or None,
    "tiktok": os.environ.get("DBOT_TT_PROXY") or None,
    "twitter": os.environ.get("DBOT_TW_PROXY") or None,
    "reddit": os.environ.get("DBOT_RD_PROXY") or None,
}

BOT_CHECK_MARKERS = ("sign in to confirm", "confirm you're not a bot",
                     "confirm you are not a bot")

LOGIN_WALL_MARKERS = (
    "redirected to the login page",
    "rate-limit for accessing posts anonymously",
    "requested content is not available, rate-limit reached",
    "login required",
    "you need to log in",
    "use --cookies",
    "--cookies-from-browser",
)


def _site_of(url: str) -> str:
    lowered = url.lower()
    for site in ("instagram", "tiktok", platforms.UNADVERTISED, "pinterest", "reddit"):
        if site in lowered:
            return site
    if platforms.UNADVERTISED_SHORT_HOST in lowered:
        return platforms.UNADVERTISED
    if "twitter.com" in lowered or "//x.com" in lowered:
        return "twitter"
    if "redd.it" in lowered:
        return "reddit"
    if "pin.it" in lowered:
        return "pinterest"
    return "other"


class BlockedBySource(Exception):
    def __init__(self, message: str, kind: str = "bot_check", site: str = "other"):
        super().__init__(message)
        self.kind = kind
        self.site = site


class TooLarge(Exception):
    pass


def _reject_long(info, *, incomplete):
    duration = info.get("duration")
    if duration and duration > MAX_DURATION_S:
        return (
            f"that clip is {int(duration) // 60} minutes long, and this bot "
            f"caps downloads at {MAX_DURATION_S // 60}"
        )
    return None


def _size_of(fmt: dict, duration) -> "int | None":
    for key in ("filesize", "filesize_approx"):
        value = fmt.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return int(value)
    rate = fmt.get("tbr")
    if isinstance(rate, (int, float)) and rate > 0 and duration:
        return int(rate * 125 * duration)
    return None


def _wanted(fmt: dict) -> bool:
    return (bool(fmt.get("format_id")) and fmt.get("ext") != "mhtml" and not fmt.get("has_drm")
            and "storyboard" not in str(fmt.get("format_note") or "").lower())


def _has(fmt: dict, kind: str) -> bool:
    return str(fmt.get(kind) or "none") != "none"


def _video_rank(fmt: dict) -> tuple:
    codec = str(fmt.get("vcodec") or "")
    plays = 2 if codec.startswith(("avc1", "h264")) else 1 if codec.startswith(("vp9", "vp09")) else 0
    return plays, min(fmt.get("height") or 0, MAX_HEIGHT), fmt.get("tbr") or 0


def _audio_rank(fmt: dict) -> tuple:
    codec = str(fmt.get("acodec") or "")
    return (1 if codec.startswith(("mp4a", "aac")) else 0), min(fmt.get("abr") or fmt.get("tbr") or 0, 192)


def _best(info: dict, budget_bytes: "float", height: "int | None" = None,
          exact: bool = False) -> "tuple | None":
    duration = info.get("duration") or 0
    formats = [fmt for fmt in info.get("formats") or () if _wanted(fmt)]
    room = budget_bytes * SIZE_MARGIN
    cap = MAX_HEIGHT if height is None else min(height, MAX_HEIGHT)
    if exact and height is not None:
        tall = lambda fmt: (fmt.get("height") or 0) == height
    else:
        tall = lambda fmt: (fmt.get("height") or 0) <= cap
    audios = sorted((fmt for fmt in formats if _has(fmt, "acodec") and not _has(fmt, "vcodec")),
                    key=_audio_rank, reverse=True)
    best = None
    for fmt in formats:
        if not _has(fmt, "vcodec") or not tall(fmt):
            continue
        size = _size_of(fmt, duration)
        if size is None:
            continue
        if _has(fmt, "acodec"):
            choice = (_video_rank(fmt) + (1, 0), fmt["format_id"], size) if size <= room else None
        else:
            choice = None
            for audio in audios:
                extra = _size_of(audio, duration)
                if extra is not None and size + extra <= room:
                    choice = (_video_rank(fmt) + _audio_rank(audio),
                              f"{fmt['format_id']}+{audio['format_id']}", size + extra)
                    break
        if choice is not None and (best is None or choice[0] > best[0]):
            best = choice
    return best


def pick_format(info: dict, budget_bytes: int, height: "int | None" = None,
                exact: bool = False) -> "str | None":
    best = _best(info, budget_bytes, height, exact)
    return None if best is None else best[1]


PICK_MIN_QUALITY = 240
PICK_MAX_QUALITIES = 5
AUDIO = "audio"


def quality_of(fmt: dict) -> int:
    label = re.match(r"(\d{3,4})p", str(fmt.get("format_note") or ""))
    if label:
        return int(label.group(1))
    width, height = fmt.get("width") or 0, fmt.get("height") or 0
    return min(width, height) if width and height else height


def _sound_rank(fmt: dict) -> tuple:
    own = (fmt.get("language_preference") or 0) >= 0
    compressed = "drc" in str(fmt.get("format_id") or "").lower()
    return (own, not compressed) + _audio_rank(fmt)


def _best_audio(formats) -> "dict | None":
    audios = [fmt for fmt in formats if _has(fmt, "acodec") and not _has(fmt, "vcodec")]
    return max(audios, key=_sound_rank) if audios else None


def _picture_of(formats, quality: int) -> "dict | None":
    pictures = [fmt for fmt in formats if _has(fmt, "vcodec") and quality_of(fmt) == quality]
    return max(pictures, key=lambda fmt: (_video_rank(fmt)[0], bool(fmt.get("filesize")),
                                          _has(fmt, "acodec"), fmt.get("tbr") or 0)) if pictures else None


def _choice_format(formats, choice, duration) -> "tuple[str, int | None] | None":
    audio = _best_audio(formats)
    if choice == AUDIO:
        return (audio["format_id"], _size_of(audio, duration)) if audio else None
    picture = _picture_of(formats, choice)
    if picture is None:
        return None
    size = _size_of(picture, duration)
    if _has(picture, "acodec"):
        return picture["format_id"], size
    if audio is None:
        return None
    extra = _size_of(audio, duration)
    return (f"{picture['format_id']}+{audio['format_id']}",
            size + extra if size is not None and extra is not None else None)


def choices_of(info: dict, free_bytes: int, paid_bytes: int = 0) -> dict:
    duration = info.get("duration") or 0
    formats = [fmt for fmt in info.get("formats") or () if _wanted(fmt)]
    free, paid = [], []
    for quality in sorted({quality_of(fmt) for fmt in formats if _has(fmt, "vcodec")}):
        if not PICK_MIN_QUALITY <= quality <= MAX_HEIGHT:
            continue
        chosen = _choice_format(formats, quality, duration)
        if not chosen or chosen[1] is None:
            continue
        if chosen[1] <= free_bytes * SIZE_MARGIN:
            free.append((quality, chosen[1], True))
        elif chosen[1] <= paid_bytes * SIZE_MARGIN:
            paid.append((quality, chosen[1], False))
    audio = _choice_format(formats, AUDIO, duration)
    fits = audio is not None and audio[1] is not None and audio[1] <= free_bytes * SIZE_MARGIN
    return {"qualities": free[-PICK_MAX_QUALITIES:] + paid[:PICK_MAX_QUALITIES],
            "audio": audio[1] if fits else None}


def pick_choice(info: dict, choice, budget_bytes: int) -> str:
    duration = info.get("duration") or 0
    formats = [fmt for fmt in info.get("formats") or () if _wanted(fmt)]
    chosen = _choice_format(formats, choice, duration)
    what = "sound" if choice == AUDIO else f"{choice}p version"
    if chosen is None:
        raise TooLarge(f"The {what} of that video is not there any more.")
    if chosen[1] is not None and chosen[1] > budget_bytes * SIZE_MARGIN:
        raise TooLarge(f"The {what} is over this bot's {budget_bytes // (1024 * 1024)} MB limit.")
    return chosen[0]


def sizes_known(info: dict) -> bool:
    duration = info.get("duration") or 0
    return any(_size_of(fmt, duration) for fmt in info.get("formats") or () if _wanted(fmt) and _has(fmt, "vcodec"))


def _format_selector(max_mb: int | None = None) -> str:
    mb = MAX_DOWNLOAD_MB if max_mb is None else max_mb
    return (
        f"bv*[filesize<{mb}M]+ba/"
        f"bv*[filesize_approx<{mb}M]+ba/"
        f"b[filesize<{mb}M]/"
        f"b[filesize_approx<{mb}M]/"
        "bv*+ba/b"
    )


def _remove_leftovers(out_dir: str, unique_id: str, keep: str | None = None) -> None:
    for path in glob.glob(os.path.join(glob.escape(out_dir), f"{unique_id}.*")):
        if keep is not None and os.path.abspath(path) == os.path.abspath(keep):
            continue
        try:
            os.remove(path)
        except OSError:
            pass


_slots = threading.Condition()
_slots_held = 0
_swapping = False
SWAP_WAIT_S = 300.0


@contextlib.contextmanager
def _slot():
    global _slots_held
    with _slots:
        while _swapping:
            _slots.wait(timeout=5.0)
        _slots_held += 1
    try:
        yield
    finally:
        with _slots:
            _slots_held -= 1
            _slots.notify_all()


def use_ytdlp_from(path: str) -> bool:
    global _swapping
    with _slots:
        _swapping = True
        try:
            deadline = time.monotonic() + SWAP_WAIT_S
            while _slots_held:
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                _slots.wait(timeout=min(left, 5.0))
            if path in sys.path:
                sys.path.remove(path)
            sys.path.insert(0, path)
            for name in [n for n in sys.modules if n == "yt_dlp" or n.startswith("yt_dlp.")]:
                del sys.modules[name]
            return True
        finally:
            _swapping = False
            _slots.notify_all()


def ytdlp_version() -> "str | None":
    for entry in sys.path:
        candidate = os.path.join(entry, "yt_dlp", "version.py")
        if os.path.isfile(candidate):
            try:
                with open(candidate, encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("__version__"):
                            return line.split("=", 1)[1].strip().strip("'\"")
            except OSError:
                return None
    return None


READ_KEEP_SECONDS = 600
READ_KEEP_MAX = 32
_read: "dict[str, tuple[float, dict]]" = {}
_read_lock = threading.Lock()


def _remember_read(url: str, info: dict) -> None:
    with _read_lock:
        now = time.monotonic()
        for key in [k for k, (at, _) in _read.items() if now - at > READ_KEEP_SECONDS]:
            del _read[key]
        while len(_read) >= READ_KEEP_MAX:
            del _read[next(iter(_read))]
        _read[url] = (now, info)


def _recall_read(url: str) -> "dict | None":
    with _read_lock:
        found = _read.get(url)
        if found is None or time.monotonic() - found[0] > READ_KEEP_SECONDS:
            return None
        return copy.deepcopy(found[1])


def _site_options(url: str) -> dict:
    opts = {}
    if YT_PLAYER_CLIENTS:
        opts["extractor_args"] = {platforms.UNADVERTISED: {"player_client": YT_PLAYER_CLIENTS}}
    site = _site_of(url)
    cookiefile = cookiefile_for(site)
    if cookiefile:
        opts["cookiefile"] = cookiefile
    proxy = SITE_PROXIES.get(site) or PROXY
    if proxy:
        opts["proxy"] = proxy
    return opts


def read_info(url: str) -> dict:
    with _slot():
        import yt_dlp
        opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "cachedir": False,
                "socket_timeout": SOCKET_TIMEOUT_S, **_site_options(url)}
        with yt_dlp.YoutubeDL(opts) as ydl:
            try:
                info = ydl.extract_info(url, download=False)
            except Exception as exc:
                found = _blocked(exc, _site_of(url))
                if found is exc:
                    raise
                raise found from exc
    if info is None or info.get("_type") in ("playlist", "multi_video"):
        raise TooLarge("That link is not one video this bot can fetch.")
    _remember_read(url, info)
    return info


def download_video(url: str, out_dir: str, max_mb: int | None = None, cancel=None,
                   height: "int | None" = None, exact: bool = False, sound: bool = False) -> str:
    mb = MAX_DOWNLOAD_MB if max_mb is None else max_mb
    with _slot():
        import yt_dlp

        os.makedirs(out_dir, exist_ok=True)
        unique_id = uuid.uuid4().hex
        outtmpl = os.path.join(out_dir, f"{unique_id}.%(ext)s")

        def stop_if_asked(*_ignored):
            if cancel is not None and cancel.is_set():
                raise yt_dlp.utils.DownloadCancelled("nobody is waiting for this download any more")

        def match_filter(info, *, incomplete):
            stop_if_asked()
            return None

        path = None
        try:
            path = _download(yt_dlp, url, mb, outtmpl, stop_if_asked, match_filter, height, exact, sound)
            stop_if_asked()
            return path
        except BaseException:
            path = None
            raise
        finally:
            _remove_leftovers(out_dir, unique_id, keep=path)


def _blocked(exc: Exception, site: str) -> Exception:
    lowered = str(exc).lower()
    if any(marker in lowered for marker in BOT_CHECK_MARKERS):
        return BlockedBySource(str(exc), "bot_check", site)
    if any(marker in lowered for marker in LOGIN_WALL_MARKERS):
        return BlockedBySource(str(exc), "login_required", site)
    return exc


def _download(yt_dlp, url: str, mb: int, outtmpl: str, progress_hook, match_filter,
              height: "int | None" = None, exact: bool = False, sound: bool = False) -> str:
    ydl_opts = {
        "outtmpl": outtmpl,
        "format": _format_selector(mb),
        "max_filesize": mb * 1024 * 1024,
        "match_filter": match_filter,
        "progress_hooks": [progress_hook],
        "socket_timeout": SOCKET_TIMEOUT_S,
        "retries": 3,
        "fragment_retries": 5,
        "concurrent_fragment_downloads": FRAGMENTS_AT_ONCE,
        "http_chunk_size": 10 * 1024 * 1024,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "noprogress": True,
        "cachedir": False,
        "merge_output_format": "mp4",
    }
    ydl_opts.update(_site_options(url))
    site = _site_of(url)
    info = _recall_read(url)
    if info is None:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(url, download=False)
            except Exception as exc:
                found = _blocked(exc, site)
                if found is exc:
                    raise
                raise found from exc
    if info is None or info.get("_type") in ("playlist", "multi_video"):
        raise TooLarge("That link is not one video this bot can fetch.")
    too_long = _reject_long(info, incomplete=False)
    if too_long:
        raise TooLarge(f"Not fetched: {too_long} minutes.")
    if sound:
        chosen = pick_choice(info, AUDIO, mb * 1024 * 1024)
    elif exact and height is not None:
        chosen = pick_choice(info, height, mb * 1024 * 1024)
    else:
        chosen = pick_format(info, mb * 1024 * 1024, height, exact)
    if chosen is None and sizes_known(info):
        raise TooLarge(f"Even its smallest version is over this bot's {mb} MB limit.")
    if chosen is not None:
        ydl_opts["format"] = chosen
        logger.info("yt-dlp: %s, %ss, format %s for a %d MB budget", site, info.get("duration"), chosen, mb)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            if site == platforms.UNADVERTISED:
                info = ydl.extract_info(url, download=True)
            else:
                info = ydl.process_ie_result(info, download=True)
        except Exception as exc:
            found = _blocked(exc, site)
            if found is exc:
                raise
            raise found from exc
        path = ydl.prepare_filename(info)
        if not os.path.exists(path):
            base, _ = os.path.splitext(path)
            mp4_path = base + ".mp4"
            if os.path.exists(mp4_path):
                path = mp4_path
    if not os.path.exists(path):
        raise TooLarge(
            f"That file is over this bot's {mb} MB limit, so the "
            "download was stopped."
        )
    return path


PROBE_SECONDS = 5
PROBE_BUDGET_SECONDS = 10
PROBE_MAX_BYTES = 64 * 1024
MAX_SIDE = 16384
MAX_SECONDS = 2 ** 31 - 1


@dataclass(frozen=True)
class VideoShape:
    width: int
    height: int
    duration: "int | None" = None


class ProbeRun:
    def __init__(self):
        self.timed_out = False
        self.spent = 0.0

    @property
    def exhausted(self) -> bool:
        return self.timed_out or self.spent >= PROBE_BUDGET_SECONDS

    def allowance(self) -> float:
        return min(PROBE_SECONDS, max(0.0, PROBE_BUDGET_SECONDS - self.spent))


def _side(value) -> "int | None":
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_SIDE:
        return None
    return value


def _quarter_turn(stream: dict) -> bool:
    turn = None
    side_data = stream.get("side_data_list")
    for data in side_data if isinstance(side_data, list) else ():
        if isinstance(data, dict) and "rotation" in data:
            turn = data["rotation"]
            break
    if turn is None:
        tags = stream.get("tags")
        turn = tags.get("rotate") if isinstance(tags, dict) else None
    try:
        turn = float(turn)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(turn):
        return False
    return round(abs(turn) / 90) % 2 == 1


def _display_width(width: int, ratio) -> int:
    if isinstance(ratio, str) and ratio.count(":") == 1:
        try:
            n, d = (int(part) for part in ratio.split(":"))
            if n > 0 and d > 0 and n != d:
                return min(MAX_SIDE, max(1, round(width * n / d)))
        except (ValueError, OverflowError):
            pass
    return width


def shape_from_ffprobe(data) -> "VideoShape | None":
    try:
        streams = data["streams"]
        stream = streams[0]
        width, height = _side(stream.get("width")), _side(stream.get("height"))
    except (AttributeError, IndexError, KeyError, TypeError):
        return None
    if width is None or height is None:
        return None
    width = _display_width(width, stream.get("sample_aspect_ratio"))
    if _quarter_turn(stream):
        width, height = height, width
    duration = None
    try:
        seconds = float(data["format"]["duration"])
        if math.isfinite(seconds) and 0 <= seconds <= MAX_SECONDS:
            duration = int(round(seconds))
    except (KeyError, TypeError, ValueError, AttributeError):
        pass
    return VideoShape(width, height, duration)


def _ffprobe_argv() -> "list[str] | None":
    found = shutil.which("ffprobe")
    return [found] if found else None


async def _read_capped(stream, limit: int) -> "bytes | None":
    data = b""
    while True:
        chunk = await stream.read(limit + 1 - len(data))
        if not chunk:
            return data
        data += chunk
        if len(data) > limit:
            return None


async def probe(path: str, run: "ProbeRun | None" = None) -> "VideoShape | None":
    argv = _ffprobe_argv()
    if argv is None:
        return None
    if run is not None and run.exhausted:
        return None
    limit = PROBE_SECONDS if run is None else run.allowance()
    started = time.monotonic()
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            *sandbox.wrap([
                *argv, "-v", "error", "-protocol_whitelist", "file", "-select_streams", "v:0",
                "-show_entries",
                "stream=width,height,sample_aspect_ratio:stream_tags=rotate:stream_side_data=rotation:format=duration",
                "-of", "json", "file:" + str(path)], read=[os.path.dirname(os.path.abspath(str(path)))]),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL)

        async def answer():
            said = await _read_capped(process.stdout, PROBE_MAX_BYTES)
            if said is not None:
                await process.wait()
            return said

        output = await asyncio.wait_for(answer(), limit)
        if output is None:
            logger.debug("ffprobe said more than %d bytes; ignored", PROBE_MAX_BYTES)
            return None
        if process.returncode != 0:
            return None
        return shape_from_ffprobe(json.loads(output))
    except asyncio.TimeoutError:
        if run is not None:
            run.timed_out = True
        logger.debug("ffprobe took more than %s s", limit)
        return None
    except Exception as exc:
        logger.debug("ffprobe failed: %s", type(exc).__name__)
        return None
    finally:
        if process is not None and process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            try:
                await process.wait()
            except Exception:  # noqa: BLE001 - nothing here may stop the delivery
                pass
        if run is not None:
            run.spent += time.monotonic() - started
