"""The yt-dlp provider: download a video with yt-dlp so the bot can re-send it.

Since v1.2.1 this is one link in resolvers.py's chain rather than the whole
download path, and it is deliberately *last* in every chain. yt-dlp makes the
request from this container's own address, and a datacenter address is the
thing Instagram and YouTube object to -- so it is the route most likely to
come back with a login page, and the only route that gets better the moment
DBOT_<SITE>_COOKIES_FILE or DBOT_<SITE>_PROXY is set. It is also the only
route that works for YouTube at all, and the only one that muxes Reddit's
separate DASH video and audio tracks.

yt-dlp is imported lazily, inside download_video(). Importing it costs
roughly 30-40 MB of resident memory and a second of startup for its
several-hundred extractor modules, and a bot that goes a whole day without
anyone pasting a link should not be paying that the whole time. The first
download pays it once, and it stays loaded from then on.
"""
import asyncio
import contextlib
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
import uuid
from dataclasses import dataclass

# Telegram itself refuses uploads over 50 MB without a local Bot API server,
# so anything larger was always going to be downloaded and then thrown away.
# Telling yt-dlp up front turns "fill the container's disk, then fail" into
# "don't start". The default leaves headroom under that 50 MB ceiling.
MAX_DOWNLOAD_MB = int(os.environ.get("DBOT_MAX_DOWNLOAD_MB", "48"))

MAX_DURATION_S = int(os.environ.get("DBOT_MAX_DURATION_SECONDS", "10800"))

MAX_HEIGHT = int(os.environ.get("DBOT_MAX_HEIGHT") or 1080)
FRAGMENTS_AT_ONCE = max(1, int(os.environ.get("DBOT_YTDLP_FRAGMENTS") or 4))
SIZE_MARGIN = 0.96

SOCKET_TIMEOUT_S = int(os.environ.get("DBOT_SOCKET_TIMEOUT", "30"))

logger = logging.getLogger(__name__)

# Empty leaves the choice to yt-dlp, which keeps up with YouTube better than a fixed list:
# by late 2026 "tv" asked for a reload and "android_vr" links answered 403.
YT_PLAYER_CLIENTS = [
    c.strip() for c in (os.environ.get("DBOT_YT_PLAYER_CLIENTS") or "").split(",") if c.strip()
]

# A cookies.txt exported from a logged-in browser is the reliable answer, and
# it is deliberately not the default: it ties this bot to a real account, the
# cookies expire in days, and the account can be banned for it. If you use
# one, use a throwaway account -- never your own.
#
# DBOT_COOKIES_FILE applies to every site. The per-site ones win where they
# are set, because the two problems want different accounts: a Google account
# for YouTube and an Instagram one for Instagram, and handing either site the
# other's cookie jar is worse than handing it none.
COOKIEFILE = os.environ.get("DBOT_COOKIES_FILE") or None
SITE_COOKIEFILES = {
    "youtube": os.environ.get("DBOT_YT_COOKIES_FILE") or None,
    "instagram": os.environ.get("DBOT_IG_COOKIES_FILE") or None,
    "tiktok": os.environ.get("DBOT_TT_COOKIES_FILE") or None,
    "twitter": os.environ.get("DBOT_TW_COOKIES_FILE") or None,
    "reddit": os.environ.get("DBOT_RD_COOKIES_FILE") or None,
}

# The same cookies.txt, pasted into a variable instead of left in a file. A
# host like Railway has environment variables and no convenient place to put a
# file, so a path-only setting meant cookies were, in practice, not an option
# there -- and cookies are the one thing that reliably gets YouTube to stop
# asking a datacenter address to sign in. The text is written to a file only
# this process can read, once, the first time a download needs it; the
# *_COOKIES_FILE settings win where both are set. (From the unmerged cloud
# session of 2026-09-25, rewritten here.)
_COOKIE_TEXT_VARS = {"youtube": "DBOT_YT_COOKIES", "instagram": "DBOT_IG_COOKIES",
                     "tiktok": "DBOT_TT_COOKIES", "twitter": "DBOT_TW_COOKIES",
                     "reddit": "DBOT_RD_COOKIES", "other": "DBOT_COOKIES"}
_cookie_text_files: dict = {}
# Sites whose variable was read, found not to be a cookies.txt, and refused:
# site -> the text that was refused. Remembered so that the answer does not
# cost a re-check and a log line on every download, and keyed on the text so
# that a changed value is looked at again.
_cookie_text_refused: dict = {}


def _cookie_text_problem(text: str) -> "str | None":
    """Why `text` is not a cookies.txt, or None if it is one.

    yt-dlp reads every line that is not blank or a comment as a cookie of
    exactly seven tab-separated fields -- and when one is not, its error
    quotes the line, and the line is a session secret, and the error goes
    into the log (review, 2026-10-08). Pasting is how it happens: a browser
    or a form turns the tabs into spaces. So the check is made here first,
    and what it says contains no part of the text: numbers only.

    "#HttpOnly_" lines are cookies (the prefix is how the format marks
    them), not comments.

    Seven fields is not enough: confirmed with yt-dlp's own loader (the
    2026.08.19 release), a domain whose flag disagrees with it, or an expiry
    that is not plain digits (-1, 1.89e9), makes the loader print the whole
    line, secret and all, in a warning or an exception. So each field is
    checked against what that loader accepts silently. A few of these
    (path, secure, name) would not print anything; they are refused anyway,
    because a line like that is not a cookie any site would take."""
    # Read the way yt-dlp reads the file: universal newlines, so a lone "\r"
    # ends a line too; and a byte-order mark at the start is not part of it.
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
        # Fixed words only, never the field itself: it is part of a secret's line.
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


# The header http.cookiejar looks for on a cookies.txt's first line.
_COOKIE_HEADER_RE = re.compile(r"#( Netscape)? HTTP Cookie File")


def _cookiefile_from_text(site: str) -> "str | None":
    var = _COOKIE_TEXT_VARS.get(site)
    text = os.environ.get(var) if var else None
    if not text or not text.strip():
        return None
    # A byte-order mark (a Windows editor's, a form's) in front of the first
    # line makes yt-dlp read that line as malformed: dropped, with a warning.
    text = text.removeprefix("\ufeff")
    if not text.strip():
        return None
    path = _cookie_text_files.get(site)
    if path and os.path.exists(path):
        return path
    if _cookie_text_refused.get(site) == os.environ.get(var):
        return None
    # Some hosts flatten a pasted multi-line value into one line with literal
    # "\n" and "\t" in it; yt-dlp would read that as one malformed cookie.
    if "\\n" in text and "\n" not in text:
        text = text.replace("\\n", "\n").replace("\\t", "\t")
    problem = _cookie_text_problem(text)
    if problem:
        _cookie_text_refused[site] = os.environ.get(var)
        logger.warning("%s is not a cookies.txt: %s. Those cookies are not used.", var, problem)
        return None
    # yt-dlp refuses the WHOLE file, quietly as far as the owner can tell, unless
    # its very first line is the format's header (checked with its own loader,
    # 2026.08.19: no header, or a blank line before it, and no cookie is read).
    # A paste often starts at the first cookie, or with an empty line.
    text = text.lstrip("\r\n \t")
    if not _COOKIE_HEADER_RE.match(text):
        text = "# Netscape HTTP Cookie File\n" + text
    fd, path = tempfile.mkstemp(prefix=f"cookies-{site}-", suffix=".txt")   # mode 0600
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text if text.endswith("\n") else text + "\n")
    _cookie_text_files[site] = path
    return path


def cookiefile_for(site: str) -> "str | None":
    """The cookie jar for this site: its own file, its own pasted text, then
    the every-site file, then the every-site text. None if there is none."""
    for candidate in (SITE_COOKIEFILES.get(site), _cookiefile_from_text(site),
                      COOKIEFILE, _cookiefile_from_text("other")):
        if candidate and os.path.exists(candidate):
            return candidate
    return None


# Where the request goes out from. The reason cookies are not always enough:
# these sites rate-limit by IP as well as by session, every cloud host's
# address is a known datacenter range, and a datacenter IP is most of why the
# server is challenged when a phone on the same link is not. A residential or
# mobile proxy is the robust answer and the only one that does not expire.
PROXY = os.environ.get("DBOT_PROXY") or None
SITE_PROXIES = {
    "youtube": os.environ.get("DBOT_YT_PROXY") or None,
    "instagram": os.environ.get("DBOT_IG_PROXY") or None,
    "tiktok": os.environ.get("DBOT_TT_PROXY") or None,
    "twitter": os.environ.get("DBOT_TW_PROXY") or None,
    "reddit": os.environ.get("DBOT_RD_PROXY") or None,
}

# What the "sign in to confirm you're not a bot" wall looks like coming back
# out of yt-dlp. Matched so the user gets a sentence instead of a stack of
# yt-dlp's documentation links.
BOT_CHECK_MARKERS = ("sign in to confirm", "confirm you're not a bot",
                     "confirm you are not a bot")

# And what a login wall looks like. Instagram stopped serving reels to
# anonymous callers from datacenter addresses, so this is now the *ordinary*
# outcome there rather than an edge case -- and until it was matched here the
# user got yt-dlp's raw text, complete with a link to its FAQ that Telegram
# then expanded into a full-width GitHub preview card. That reads as the bot
# having crashed and helpfully shown you its documentation.
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
    """Which per-site cookie jar and proxy apply. The split matters: the
    YouTube problem wants a Google account and the Instagram one wants an
    Instagram account, and handing either site the other's cookies is worse
    than handing it none."""
    lowered = url.lower()
    for site in ("instagram", "tiktok", "youtube", "pinterest", "reddit"):
        if site in lowered:
            return site
    if "youtu.be" in lowered:
        return "youtube"
    if "twitter.com" in lowered or "//x.com" in lowered:
        return "twitter"
    if "redd.it" in lowered:
        return "reddit"
    if "pin.it" in lowered:
        return "pinterest"
    return "other"


class BlockedBySource(Exception):
    """The site refused the server, not the link.

    `kind` says which way it refused, because the two want different
    sentences: "bot_check" is a challenge that may pass on a retry, and
    "login_required" will not pass on any retry -- it needs a credential this
    bot does not have, and saying "try again later" about it is a lie.
    """

    def __init__(self, message: str, kind: str = "bot_check", site: str = "other"):
        super().__init__(message)
        self.kind = kind
        self.site = site


class TooLarge(Exception):
    """The clip is over the size/duration ceiling. Message is user-facing."""


def _reject_long(info, *, incomplete):
    """yt-dlp's match_filter hook: runs on the *metadata*, before a single
    byte of media is fetched."""
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


def pick_format(info: dict, budget_bytes: int) -> "str | None":
    duration = info.get("duration") or 0
    formats = [fmt for fmt in info.get("formats") or () if _wanted(fmt)]
    room = budget_bytes * SIZE_MARGIN
    tall = lambda fmt: (fmt.get("height") or 0) <= MAX_HEIGHT
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
            choice = (_video_rank(fmt) + (1, 0), fmt["format_id"]) if size <= room else None
        else:
            choice = None
            for audio in audios:
                extra = _size_of(audio, duration)
                if extra is not None and size + extra <= room:
                    choice = (_video_rank(fmt) + _audio_rank(audio), f"{fmt['format_id']}+{audio['format_id']}")
                    break
        if choice is not None and (best is None or choice[0] > best[0]):
            best = choice
    return None if best is None else best[1]


def sizes_known(info: dict) -> bool:
    duration = info.get("duration") or 0
    return any(_size_of(fmt, duration) for fmt in info.get("formats") or () if _wanted(fmt) and _has(fmt, "vcodec"))


def _format_selector(max_mb: int | None = None) -> str:
    mb = MAX_DOWNLOAD_MB if max_mb is None else max_mb
    return (
        # A video stream that fits, plus the best audio, merged.
        f"bv*[filesize<{mb}M]+ba/"
        f"bv*[filesize_approx<{mb}M]+ba/"
        # Or a single already-muxed stream that fits.
        f"b[filesize<{mb}M]/"
        f"b[filesize_approx<{mb}M]/"
        # Nothing advertised a size it could keep to: take the best merge, or
        # the best muxed stream, and let max_filesize stop it if it runs long.
        "bv*+ba/b"
    )


def _remove_leftovers(out_dir: str, unique_id: str, keep: str | None = None) -> None:
    """Everything yt-dlp wrote for this download except `keep`.

    A download that does not finish leaves what it had: `<id>.mp4.part`, the
    video and audio halves of a merge (`<id>.f137.mp4`, `<id>.f140.m4a`), a
    `.ytdl` state file. Nothing else knows those names, so nothing else
    removed them -- and a container that runs for months on an ephemeral disk
    is filled by the ones a failed or timed-out download left behind."""
    for path in glob.glob(os.path.join(glob.escape(out_dir), f"{unique_id}.*")):
        if keep is not None and os.path.abspath(path) == os.path.abspath(keep):
            continue
        try:
            os.remove(path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Replacing yt-dlp while the bot runs
# ---------------------------------------------------------------------------
# yt-dlp is the route that rots: the sites change under it every few weeks and
# the image has whatever version its last build installed (routewatch.py says
# when that is behind, and can fetch the newer one). Putting a newer copy in
# place means swapping modules in a process that may be using the old ones --
# harmless between downloads, a mixed-version crash in the middle of one. So a
# download holds a *slot* for as long as it runs, and a swap waits until none
# is held and keeps new ones out while it happens.
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
    """Make `path` (a folder holding a newer yt_dlp package) the one every
    later download imports. Blocking. Returns False, and changes nothing, if
    downloads did not finish within SWAP_WAIT_S -- the caller tries again the
    next day."""
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
            # What the old copy had imported must go, or the next `import
            # yt_dlp` is satisfied from sys.modules and nothing has changed.
            for name in [n for n in sys.modules if n == "yt_dlp" or n.startswith("yt_dlp.")]:
                del sys.modules[name]
            return True
        finally:
            _swapping = False
            _slots.notify_all()


def ytdlp_version() -> "str | None":
    """The version a download would use right now, without importing it."""
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


def download_video(url: str, out_dir: str, max_mb: int | None = None, cancel=None) -> str:
    """Downloads the video at `url` into `out_dir`, returns the local file path.
    `max_mb` is the ceiling for this one download -- MAX_DOWNLOAD_MB unless
    somebody has switched on large files (large_files.py).

    `cancel` is a threading.Event: set it and the download stops at its next
    chunk and removes what it had written. A thread cannot be cancelled, only
    asked, and a caller that stops waiting (resolvers.py gives a route a time
    limit) would otherwise leave the download running to its end, holding
    bandwidth and disk for a file nobody will collect.

    Whatever ends the download -- it finishing, failing or being stopped --
    leaves only the file it returns.

    Blocking -- call it through asyncio.to_thread.
    """
    mb = MAX_DOWNLOAD_MB if max_mb is None else max_mb
    with _slot():
        import yt_dlp  # deferred: see the module docstring

        os.makedirs(out_dir, exist_ok=True)
        unique_id = uuid.uuid4().hex
        outtmpl = os.path.join(out_dir, f"{unique_id}.%(ext)s")

        def stop_if_asked(*_ignored):
            if cancel is not None and cancel.is_set():
                raise yt_dlp.utils.DownloadCancelled("nobody is waiting for this download any more")

        def match_filter(info, *, incomplete):
            # Asked once the page has been read and before any media is fetched: a
            # request that is already abandoned stops there.
            stop_if_asked()
            return None

        path = None
        try:
            path = _download(yt_dlp, url, mb, outtmpl, stop_if_asked, match_filter)
            stop_if_asked()          # finished, but too late for anyone to use it
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


def _download(yt_dlp, url: str, mb: int, outtmpl: str, progress_hook, match_filter) -> str:
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
        # Nothing here reads a cache, and the default location is a writeable
        # directory this process would otherwise keep growing.
        "cachedir": False,
        "merge_output_format": "mp4",
    }
    if YT_PLAYER_CLIENTS:
        ydl_opts["extractor_args"] = {"youtube": {"player_client": YT_PLAYER_CLIENTS}}
    site = _site_of(url)
    cookiefile = cookiefile_for(site)
    if cookiefile:
        ydl_opts["cookiefile"] = cookiefile
    proxy = SITE_PROXIES.get(site) or PROXY
    if proxy:
        ydl_opts["proxy"] = proxy
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
    chosen = pick_format(info, mb * 1024 * 1024)
    if chosen is None and sizes_known(info):
        raise TooLarge(f"Even its smallest version is over this bot's {mb} MB limit.")
    if chosen is not None:
        ydl_opts["format"] = chosen
        logger.info("yt-dlp: %s, %ss, format %s for a %d MB budget", site, info.get("duration"), chosen, mb)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.process_ie_result(info, download=True)
        except Exception as exc:
            found = _blocked(exc, site)
            if found is exc:
                raise
            raise found from exc
        path = ydl.prepare_filename(info)
        # merge_output_format can change the extension after download
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


# ---------------------------------------------------------------------------
# The shape of a video, for the send
# ---------------------------------------------------------------------------
# Telegram draws a video from the width, height and duration the sender gives
# it. Left out, the clients fall back to a 1:1 square and crop into it, so a
# 9:16 reel arrived as a square (reported by the owner's friend, 2026-10-12).
# bot.py's _send_media measures each video it sends as a video, with the ffprobe
# that comes with ffmpeg, and passes the three numbers. It is a nicety and never
# a gate: whatever goes wrong here, the video is sent exactly as it was before.
PROBE_SECONDS = 5
# And one delivery (an album, a large download's items) gets this much probing in
# all, however many videos it holds; past it the rest are sent without measuring.
PROBE_BUDGET_SECONDS = 10
PROBE_MAX_BYTES = 64 * 1024
MAX_SIDE = 16384
MAX_SECONDS = 2 ** 31 - 1      # a file can claim any length; Telegram's field is a 32-bit integer


@dataclass(frozen=True)
class VideoShape:
    """What the player will show: the DISPLAYED size (the stored size, widened
    by a non-square pixel and turned a quarter when the file says so) and the
    length in whole seconds, or None when ffprobe did not say."""
    width: int
    height: int
    duration: "int | None" = None


class ProbeRun:
    """One delivery's probing. `timed_out` is set by probe() when ffprobe was
    killed for taking too long, so that the rest of an album is sent without
    asking again: a hostile album must not cost PROBE_SECONDS for each file. And
    `spent` adds up the seconds every probe of the delivery has taken, which may
    not pass PROBE_BUDGET_SECONDS in all: slow-but-not-stalled files cannot add up
    to a long wait either."""

    def __init__(self):
        self.timed_out = False
        self.spent = 0.0

    @property
    def exhausted(self) -> bool:
        """No more probing for this delivery."""
        return self.timed_out or self.spent >= PROBE_BUDGET_SECONDS

    def allowance(self) -> float:
        """How long the next probe may take: its own limit, or what is left of
        the delivery's budget if that is less."""
        return min(PROBE_SECONDS, max(0.0, PROBE_BUDGET_SECONDS - self.spent))


def _side(value) -> "int | None":
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_SIDE:
        return None
    return value


def _quarter_turn(stream: dict) -> bool:
    """Whether the player turns the picture 90 or 270 degrees, which swaps its
    sides. The display matrix (side data) is asked first and the older `rotate`
    tag only when there is none, so a file that carries both is not turned
    twice. ffprobe rounds (89.99999 happens), hence round() and not int()."""
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
    """`width` widened by a non-square pixel ("N:D"), as the player shows it."""
    if isinstance(ratio, str) and ratio.count(":") == 1:
        try:
            n, d = (int(part) for part in ratio.split(":"))
            if n > 0 and d > 0 and n != d:
                return min(MAX_SIDE, max(1, round(width * n / d)))
        except (ValueError, OverflowError):
            pass
    return width


def shape_from_ffprobe(data) -> "VideoShape | None":
    """The shape from ffprobe's JSON, or None for anything that is not a
    plausible picture. Pure."""
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
    """The command that starts ffprobe, or None. There is no configured ffmpeg
    location in this bot (yt-dlp finds ffmpeg on PATH, and the image installs
    both programs from Debian's one `ffmpeg` package), so ffprobe is found the
    same way."""
    found = shutil.which("ffprobe")
    return [found] if found else None


async def _read_capped(stream, limit: int) -> "bytes | None":
    """Everything on `stream`, or None once it is more than `limit` bytes."""
    data = b""
    while True:
        chunk = await stream.read(limit + 1 - len(data))
        if not chunk:
            return data
        data += chunk
        if len(data) > limit:
            return None


async def probe(path: str, run: "ProbeRun | None" = None) -> "VideoShape | None":
    """The width, height and duration of the video at `path`, or None.

    One ffprobe, started without a shell, with nothing to read from and nothing
    to say; killed and reaped after PROBE_SECONDS. `file:` and a protocol
    whitelist because ffprobe goes by what a file CONTAINS, not what it is
    called: a downloaded "video" that is really a playlist must not make this
    server fetch anything. Never raises, and logs numbers only: no path, no link."""
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
            *argv, "-v", "error", "-protocol_whitelist", "file", "-select_streams", "v:0",
            "-show_entries",
            "stream=width,height,sample_aspect_ratio:stream_tags=rotate:stream_side_data=rotation:format=duration",
            "-of", "json", "file:" + str(path),
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
            # Out of time, over the cap, or cancelled: it is killed and its exit
            # collected, so that no child is left running or unreaped.
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
