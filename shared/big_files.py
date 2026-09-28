"""Files over the Bot API's size limits, moved over MTProto instead.

The cloud Bot API will not let a bot download anything over 20 MB or send
anything over 50 MB. Those are limits of the Bot API, not of Telegram: the
Bot API is itself only a client of MTProto, Telegram's own protocol, and over
MTProto a bot may move files of up to 2000 MB in either direction. So files
past the Bot API's limits come and go over MTProto, logged in as this same bot
with its own token, and everything else -- every update, every message, every
file under the limits -- stays exactly where it was.

Why not a local Bot API server, which lifts the same limits and which
LOCAL_BOT_API_URL has understood since 1.0: CHANGES_v2.0.0.md has the whole
argument. In short, its limits only lift in --local mode, where it hands back
paths on its own disk, so on Railway it has to live inside this container as
a second process; switching to it means logging the bot out of Telegram's
cloud, with ten minutes before it can come back; and while it is down the bot
hears nothing at all. This route is only ever used for a big file, and when it
fails, the bot is the bot it was before, refusing files over 20 MB.

Off unless the bot's API id and hash are set (my.telegram.org -> API
development tools): <PREFIX>_API_ID and <PREFIX>_API_HASH, or TELEGRAM_API_ID
and TELEGRAM_API_HASH once for every bot, in shared/.env. They are the
Telegram *app's* credentials, not the bot's, so one pair serves all of them.
With a local Bot API server configured this module is never asked: that
server carries big files itself.

SHARED SINCE 2.0.0. ConvertBot and DownloaderBot both move big files, so this
module lives in shared/ and each bot says who it is, once, before using it:
big_files.configure("CBOT", "convertbot"). The login is per bot token, and so
is the session file.

EACH TRANSFER IS A PROCESS OF ITS OWN, like each conversion (convert_worker.py).
Measured on 2026-09-26: importing Telethon costs the process 26.6 MB, and
connecting 4 MB more -- and Python never hands an imported library back, so
the first big file would have left ConvertBot 31 MB heavier for as long as it
ran, on a host that bills resident memory. So the bot process never imports
Telethon at all. A transfer runs this same file as a child (`python
big_files.py <job>`), which reports progress and its outcome as JSON lines on
stdout and exits, giving everything back. Starting one costs about a second,
against transfers that take many.

What the route does not do:

- It never receives updates. The session asks Telegram not to send any
  (receive_updates=False), so it cannot take an update away from polling.
- It does not log in for every file. The login (an MTProto auth key, as good
  as the token) is saved to one file in the container's temporary directory,
  readable only by the bot's own user, and reused by the next transfer. It
  never goes into the database, where it would travel in every /dbdump, and it
  goes with the container. A crash loop never logs in at all: nothing does
  until somebody sends a big file.
- It is used in private chats only. A message id in a private chat is the
  bot's own, so the message can be fetched by id alone; a group would need
  more than that to find it, and a group is not where files get converted.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_MB = 1024 * 1024

# What the cloud Bot API lets a bot do.
BOT_API_DOWNLOAD_BYTES = 20 * _MB
BOT_API_SEND_BYTES = 50 * _MB
# What MTProto lets a bot do, both ways. (4000 MB is for Premium accounts,
# which a bot is not.)
MTPROTO_MAX_BYTES = 2000 * _MB

# How long connecting and logging in may take before the attempt is given up.
CONNECT_TIMEOUT_S = 20
# After a failed connection, how long every big file is refused straight away
# rather than each one waiting out CONNECT_TIMEOUT_S to fail the same way.
REST_AFTER_FAILURE_S = 300
# Telegram serves a file in parts of at most 512 KB a request, and a
# transfer that asks for one part at a time waits a round trip for each.
# Telethon's own choice for anything under 100 MB is 128 KB, one after
# another -- which is what held the owner's laptop, 260 ms from Telegram's
# servers, to half a megabyte a second. So parts are the largest there are,
# and a download keeps PARALLEL_PARTS of them in flight at once.
PART_BYTES = 512 * 1024
PARALLEL_PARTS = 4
# A transfer that is still moving is left to finish; one that has moved
# nothing for STALL_S has stalled, and is stopped.
STALL_S = float(os.environ.get("BIG_FILES_STALL_SECONDS") or 90)
# And an upper bound all the same, for a transfer that creeps: ten minutes,
# or the file at 256 KB a second if that is longer -- two hours for the
# largest there is, which only a transfer still moving would reach.
TRANSFER_TIME_LIMIT_S = 600
MIN_RATE_BYTES_S = 256 * 1024
# How often the child reports how far it has got.
PROGRESS_EVERY_S = 0.5


def transfer_limit_s(size: int) -> int:
    """The longest a transfer of `size` bytes may take."""
    return int(max(TRANSFER_TIME_LIMIT_S, (size or 0) / MIN_RATE_BYTES_S))

# Set by configure(): whose token this route logs in with, and through
# which app.
PREFIX = ""
API_ID = ""
API_HASH = ""
BOT_TOKEN = ""

# The file the child runs as. The real path, not __file__: in the one-process
# edition a module's __file__ is its bot's home directory.
SCRIPT = Path(__spec__.origin if __spec__ else __file__).resolve()
# One per token, so a new token is never handed an old login.
SESSION_FILE = Path(tempfile.gettempdir()) / "unconfigured-mtproto.session"


class Unavailable(Exception):
    """The MTProto route could not be used. Nothing about the file is known to be wrong -- it is the route that is down."""


class Stalled(asyncio.TimeoutError):
    """The transfer stopped moving: nothing arrived or left for STALL_S. A
    TimeoutError too, so whoever only cares that it ran out of time still
    catches it."""


class NotThere(Exception):
    """The route works, but the message or its file is not what was asked
    for: deleted since, or a different file than the one announced."""


def _telethon_installed() -> bool:
    try:
        return importlib.util.find_spec("telethon") is not None
    except (ImportError, ValueError):
        return False


def _configured() -> bool:
    if not (API_ID.strip().isdigit() and API_HASH.strip() and BOT_TOKEN):
        return False
    if not _telethon_installed():
        logger.warning("%s_API_ID and %s_API_HASH are set but Telethon is not installed; "
                       "files over the Bot API's limits stay refused.", PREFIX, PREFIX)
        return False
    return True


# Decided in configure(): whether the route exists at all. Whether it is
# *working* is _resting_until's -- except that verify() switches it off for
# good when Telegram refuses the API id and hash themselves.
CONFIGURED = False
# What verify() found, as the bot started: None until it has asked, then
# whether Telegram accepted the API id and hash.
VERIFIED = None
_on_disabled: list = []
# What Telegram says when the credentials themselves are wrong -- as opposed
# to a network that is not answering, which says nothing about them.
_REFUSALS = ("ApiIdInvalid", "api_id/api_hash", "AccessTokenInvalid", "AccessTokenExpired")


def configure(prefix: str, name: str) -> bool:
    """Which bot this is: its settings' prefix ("CBOT") and its name
    ("convertbot"), for its token, its API id and its session file. Returns
    whether the route is on."""
    global PREFIX, API_ID, API_HASH, BOT_TOKEN, SESSION_FILE, CONFIGURED
    PREFIX = prefix
    API_ID = os.environ.get(f"{prefix}_API_ID") or os.environ.get("TELEGRAM_API_ID") or ""
    API_HASH = os.environ.get(f"{prefix}_API_HASH") or os.environ.get("TELEGRAM_API_HASH") or ""
    BOT_TOKEN = os.environ.get(f"{prefix}_TOKEN") or ""
    # One per token, so a new token is never handed an old login.
    SESSION_FILE = Path(tempfile.gettempdir()) / (
        f"{name}-mtproto-" + hashlib.sha256(BOT_TOKEN.encode()).hexdigest()[:12] + ".session")
    CONFIGURED = _configured()
    return CONFIGURED


def usable(chat_id: int) -> bool:
    """Whether a big file in this chat can go over MTProto. Private chats
    only: a user's id is positive, a group's is negative."""
    return CONFIGURED and chat_id > 0


def describe() -> str:
    """One line for the startup log."""
    if VERIFIED is False:
        return (f"files past the Bot API's limits are refused: Telegram refused the API id and hash "
                f"({PREFIX}_API_ID / {PREFIX}_API_HASH, or TELEGRAM_API_ID / TELEGRAM_API_HASH)")
    if CONFIGURED:
        return f"files past the Bot API's limits go over MTProto ({PREFIX}_API_ID or TELEGRAM_API_ID is set)"
    return (f"files past the Bot API's limits are refused (set {PREFIX}_API_ID and {PREFIX}_API_HASH, "
            f"or TELEGRAM_API_ID and TELEGRAM_API_HASH, to lift them)")


def on_disabled(hook) -> None:
    """Call `hook()` if verify() switches the route off -- for a module that
    worked out a limit from CONFIGURED when it was imported."""
    _on_disabled.append(hook)


async def verify() -> bool:
    """Log in once, as the bot starts, so that an API id and hash that are set
    but wrong switch the route off -- the bot then promises and takes exactly
    what the Bot API allows, as if they had never been set -- instead of
    failing the first person who sends a big file. A network that does not
    answer is not a verdict: the route stays on, and the next transfer finds
    out. Returns whether the route is on. Never raises."""
    global CONFIGURED, VERIFIED
    if not CONFIGURED:
        return False
    try:
        await _run({"op": "check", "limit_s": CONNECT_TIMEOUT_S})
    except Unavailable as exc:
        if not any(word in str(exc) for word in _REFUSALS):
            logger.warning("Could not check the MTProto login for big files (%s); the route stays on.", exc)
            return True
        CONFIGURED, VERIFIED = False, False
        logger.warning("Telegram refused %s's API id and hash for big files (%s). Files past the Bot API's "
                       "limits are refused until they are fixed.", PREFIX, exc)
        for hook in _on_disabled:
            try:
                hook()
            except Exception:
                logger.exception("A module could not lower its limits after the big-file route went off")
        return False
    except Exception as exc:
        if not _closing:   # stopped by close(): the bot is shutting down, not the route failing
            logger.warning("Could not check the MTProto login for big files (%s: %s); the route stays on.",
                           type(exc).__name__, exc)
        return True
    VERIFIED = True
    logger.info("MTProto login for big files checked: Telegram accepted it.")
    return True


# ---------------------------------------------------------------------------
# The bot's side: start a child, follow it, say how it ended
# ---------------------------------------------------------------------------

_resting_until = 0.0
_children: set = set()
_closing = False


def _child_environment() -> dict:
    """What the child needs, handed over explicitly and under names of its
    own: in the one-process edition this module sees its bot's settings
    through a view of its own, the child inherits only the real environment,
    and the child has no way to know which bot started it."""
    env = {key: value for key, value in dict(os.environ).items() if isinstance(value, str)}
    env.update(BIG_FILES_API_ID=API_ID, BIG_FILES_API_HASH=API_HASH, BIG_FILES_TOKEN=BOT_TOKEN,
               BIG_FILES_SESSION=str(SESSION_FILE), PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    return env


async def _run(job: dict, progress=None) -> dict:
    """Run one transfer in a child and return its last line. Raises
    Unavailable, NotThere, asyncio.TimeoutError past the limit, or
    RuntimeError for anything else the child reported."""
    global _resting_until
    if not CONFIGURED:
        raise Unavailable(f"{PREFIX or 'the bot'}'s API id and hash are not set")
    if time.monotonic() < _resting_until:
        raise Unavailable("resting after a failed connection")
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-u", str(SCRIPT), json.dumps(job),
        cwd=str(SCRIPT.parent), env=_child_environment(),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _children.add(proc)
    outcome = None
    errors = bytearray()

    async def keep_stderr_tail():
        # Read as it comes, and in chunks rather than lines: a chatty library
        # must not fill the pipe and stall the child, and one line longer than
        # the stream's 64 KB limit would kill a line-by-line reader and leave
        # wait() waiting for a pipe nobody empties. Only the end is reported.
        while chunk := await proc.stderr.read(65536):
            errors.extend(chunk)
            del errors[:-2000]

    async def follow():
        nonlocal outcome
        drain = asyncio.ensure_future(keep_stderr_tail())
        while True:
            try:
                raw = await proc.stdout.readline()
            except ValueError:
                continue          # a line over the limit: not one of ours; readline dropped it
            if not raw:
                break
            try:
                line = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue
            if "progress" in line:
                if progress is not None:
                    result = progress(*line["progress"])
                    if asyncio.iscoroutine(result):
                        await result
            else:
                outcome = line
        await proc.wait()
        await drain

    try:
        await asyncio.wait_for(follow(), job["limit_s"] + 2 * CONNECT_TIMEOUT_S)
    except BaseException:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        raise
    finally:
        _children.discard(proc)

    if outcome is None:
        tail = bytes(errors).decode("utf-8", "replace").strip()[-300:]
        raise RuntimeError(f"the transfer process exited {proc.returncode} without a result. {tail}".strip())
    if outcome.get("ok"):
        return outcome
    kind, message = outcome.get("kind"), outcome.get("message") or ""
    if kind == "unavailable":
        _resting_until = time.monotonic() + REST_AFTER_FAILURE_S
        logger.warning("MTProto route for big files failed: %s", message)
        raise Unavailable(message)
    if kind == "not_there":
        raise NotThere(message)
    if kind == "stalled":
        raise Stalled(message)
    if kind == "timeout":
        raise asyncio.TimeoutError(message)
    raise RuntimeError(message)


async def download(chat_id: int, message_id: int, dest, expected_size: int = 0,
                   progress=None) -> int:
    """Write the file attached to message `message_id` in the private chat
    `chat_id` to `dest`. Returns the number of bytes written.

    `progress(done, total)` is called as it arrives, and may be a coroutine.
    Raises Unavailable when the route is down, NotThere when the message is,
    and asyncio.TimeoutError when the transfer stalls or runs past
    transfer_limit_s. A partial file is left for the caller, which owns
    `dest`, to remove.
    """
    if not usable(chat_id):
        raise Unavailable("big files are only fetched in private chats")
    outcome = await _run({"op": "download", "chat_id": chat_id, "message_id": message_id,
                          "dest": str(dest), "expected_size": expected_size,
                          "limit_s": transfer_limit_s(expected_size)}, progress)
    return int(outcome.get("bytes") or 0)


async def send_document(chat_id: int, path, filename: str, caption: str = "", progress=None) -> None:
    """Send `path` to the private chat `chat_id` as a document called
    `filename`, with a plain-text caption. `progress(done, total)` as it goes
    up. Raises Unavailable when the route is down; anything Telegram says
    about the upload itself is a RuntimeError."""
    if not usable(chat_id):
        raise Unavailable("big files are only sent in private chats")
    await _run({"op": "send", "chat_id": chat_id, "path": str(path), "filename": filename, "caption": caption,
                "limit_s": transfer_limit_s(Path(path).stat().st_size)}, progress)


async def close() -> None:
    """Stop any transfer still running -- the bot is shutting down. Never raises."""
    global _closing
    _closing = True
    for proc in list(_children):
        try:
            if proc.returncode is None:
                proc.kill()
        except Exception:
            logger.debug("Could not stop a transfer process", exc_info=True)


# ---------------------------------------------------------------------------
# The child's side: one transfer, then exit
# ---------------------------------------------------------------------------

def _emit(line: dict) -> None:
    sys.stdout.write(json.dumps(line) + "\n")
    sys.stdout.flush()


def _save_session(text: str) -> None:
    """Written whole and then renamed, so a reader never sees half a file."""
    partial = SESSION_FILE.with_suffix(".partial")
    partial.write_text(text, encoding="utf-8")
    try:
        os.chmod(partial, 0o600)
    except OSError:
        pass
    os.replace(partial, SESSION_FILE)


async def _connect():
    """A connected, logged-in client. Raises Unavailable."""
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    try:
        saved = SESSION_FILE.read_text(encoding="utf-8") if SESSION_FILE.is_file() else ""
    except OSError:
        saved = ""
    client = TelegramClient(StringSession(saved or None), int(API_ID), API_HASH.strip(),
                            receive_updates=False, connection_retries=1, request_retries=2,
                            timeout=CONNECT_TIMEOUT_S)
    try:
        await asyncio.wait_for(client.connect(), CONNECT_TIMEOUT_S)
        if not await client.is_user_authorized():
            await asyncio.wait_for(client.sign_in(bot_token=BOT_TOKEN), CONNECT_TIMEOUT_S)
            _save_session(client.session.save())
    except Exception as exc:
        try:
            await client.disconnect()
        except Exception:
            pass
        raise Unavailable(f"{type(exc).__name__}: {exc}") from exc
    return client


def _media_size(media) -> int:
    document = getattr(media, "document", None)
    return int(getattr(document, "size", 0) or 0)


class _Stalled(Exception):
    pass


class _Moving:
    """When the transfer last moved a byte."""

    def __init__(self):
        self.at = time.monotonic()


def _progress_emitter(moving: "_Moving | None" = None):
    last = [0.0]

    def progress(done, total):
        now = time.monotonic()
        if moving is not None:
            moving.at = now
        if now - last[0] >= PROGRESS_EVERY_S:
            last[0] = now
            _emit({"progress": [done, total]})
    return progress


async def _watched(coroutine, limit_s: float, moving: _Moving):
    """Run a transfer to its end, unless nothing moves for STALL_S or it
    runs past `limit_s`."""
    task = asyncio.ensure_future(coroutine)
    deadline = time.monotonic() + limit_s
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=min(5.0, STALL_S / 3))
            if done:
                return task.result()
            now = time.monotonic()
            if now - moving.at > STALL_S:
                raise _Stalled(f"nothing moved for {STALL_S:.0f}s")
            if now > deadline:
                raise asyncio.TimeoutError(f"still going after {limit_s / 60:.0f} minutes")
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except BaseException:
                pass


async def _download_in_parallel(client, media, handle, size: int, progress) -> None:
    """PARALLEL_PARTS streams, each asking for every PARALLEL_PARTS-th part of
    PART_BYTES, written where it belongs as it arrives."""
    streams = max(1, min(PARALLEL_PARTS, -(-size // PART_BYTES)))
    done = 0

    async def stream(index: int) -> None:
        nonlocal done
        position = index * PART_BYTES
        async for chunk in client.iter_download(media, offset=position, stride=streams * PART_BYTES,
                                                request_size=PART_BYTES, file_size=size):
            handle.seek(position)
            handle.write(chunk)
            position += streams * PART_BYTES
            done += len(chunk)
            progress(done, size)
    await asyncio.gather(*(stream(index) for index in range(streams)))


async def _child_download(client, job: dict, progress) -> int:
    chat_id, dest, expected = job["chat_id"], job["dest"], job.get("expected_size") or 0
    try:
        # No chat given: for anything but a channel that is
        # messages.getMessages, which finds a message by id alone.
        message = await client.get_messages(None, ids=job["message_id"])
    except Exception as exc:
        raise Unavailable(f"{type(exc).__name__}: {exc}") from exc
    if message is None or getattr(message, "media", None) is None:
        raise NotThere("the message with the file is no longer in the chat")
    # Message ids in private chats belong to the bot's whole account, so the
    # id alone finds the message -- and so it is checked to be in the chat it
    # was sent in, and to carry the file that was announced.
    if getattr(message.peer_id, "user_id", None) != chat_id:
        raise NotThere("the message is not in this chat")
    size = _media_size(message.media)
    if expected and size and size != expected:
        raise NotThere(f"the message holds a {size}-byte file, not the {expected} bytes announced")
    with open(dest, "wb") as handle:
        if size:
            await _download_in_parallel(client, message.media, handle, size, progress)
        else:
            await client.download_media(message, file=handle, progress_callback=progress)
    written = Path(dest).stat().st_size
    if expected and written != expected:
        raise NotThere(f"only {written} of {expected} bytes arrived")
    return written


async def _child_send(client, job: dict, progress) -> None:
    from telethon import types
    # Uploaded first, in the largest parts Telegram takes, then sent: left to
    # send_file, anything under 100 MB would go up 128 KB at a time.
    uploaded = await client.upload_file(job["path"], part_size_kb=PART_BYTES // 1024,
                                        file_name=job["filename"], progress_callback=progress)
    await client.send_file(
        types.PeerUser(job["chat_id"]), file=uploaded, caption=job.get("caption") or None,
        force_document=True, parse_mode=None,
        attributes=[types.DocumentAttributeFilename(job["filename"])])


async def _child(job: dict) -> None:
    try:
        client = await _connect()
    except Unavailable as exc:
        _emit({"ok": False, "kind": "unavailable", "message": str(exc)})
        return
    moving = _Moving()
    progress = _progress_emitter(moving)
    limit_s = job.get("limit_s") or TRANSFER_TIME_LIMIT_S
    try:
        if job["op"] == "check":
            # verify(): the login alone, and Telegram's own view of who it is.
            await asyncio.wait_for(client.get_me(), CONNECT_TIMEOUT_S)
            _emit({"ok": True})
        elif job["op"] == "download":
            written = await _watched(_child_download(client, job, progress), limit_s, moving)
            _emit({"ok": True, "bytes": written})
        else:
            await _watched(_child_send(client, job, progress), limit_s, moving)
            _emit({"ok": True})
    except _Stalled as exc:
        _emit({"ok": False, "kind": "stalled", "message": str(exc)})
    except asyncio.TimeoutError as exc:
        _emit({"ok": False, "kind": "timeout", "message": str(exc) or "ran out of time"})
    except Unavailable as exc:
        _emit({"ok": False, "kind": "unavailable", "message": str(exc)})
    except NotThere as exc:
        _emit({"ok": False, "kind": "not_there", "message": str(exc)})
    except Exception as exc:
        _emit({"ok": False, "kind": "error", "message": f"{type(exc).__name__}: {exc}"})
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    # The child: told everything by the bot that started it.
    API_ID = os.environ.get("BIG_FILES_API_ID", "")
    API_HASH = os.environ.get("BIG_FILES_API_HASH", "")
    BOT_TOKEN = os.environ.get("BIG_FILES_TOKEN", "")
    SESSION_FILE = Path(os.environ.get("BIG_FILES_SESSION") or SESSION_FILE)
    asyncio.run(_child(json.loads(sys.argv[1])))
