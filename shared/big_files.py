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

BOT_API_DOWNLOAD_BYTES = 20 * _MB
BOT_API_SEND_BYTES = 50 * _MB
MTPROTO_MAX_BYTES = 2000 * _MB

CONNECT_TIMEOUT_S = 20
REST_AFTER_FAILURE_S = 300
PART_BYTES = 512 * 1024
PARALLEL_PARTS = 4
STALL_S = float(os.environ.get("BIG_FILES_STALL_SECONDS") or 90)
TRANSFER_TIME_LIMIT_S = 600
MIN_RATE_BYTES_S = 256 * 1024
PROGRESS_EVERY_S = 0.5


def transfer_limit_s(size: int) -> int:
    return int(max(TRANSFER_TIME_LIMIT_S, (size or 0) / MIN_RATE_BYTES_S))


PREFIX = ""
API_ID = ""
API_HASH = ""
BOT_TOKEN = ""

SCRIPT = Path(__spec__.origin if __spec__ else __file__).resolve()
CHILD = (next(p for p in SCRIPT.parents if (p / "main.py").is_file()) / "main.py", "--module", "big_files")
SESSION_FILE = Path(tempfile.gettempdir()) / "unconfigured-mtproto.session"


class Unavailable(Exception):
    pass


class Stalled(asyncio.TimeoutError):
    pass


class NotThere(Exception):
    pass


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


CONFIGURED = False
VERIFIED = None
_on_disabled: list = []
_REFUSALS = ("ApiIdInvalid", "api_id/api_hash", "AccessTokenInvalid", "AccessTokenExpired")


def configure(prefix: str, name: str) -> bool:
    global PREFIX, API_ID, API_HASH, BOT_TOKEN, SESSION_FILE, CONFIGURED
    PREFIX = prefix
    API_ID = os.environ.get(f"{prefix}_API_ID") or os.environ.get("TELEGRAM_API_ID") or ""
    API_HASH = os.environ.get(f"{prefix}_API_HASH") or os.environ.get("TELEGRAM_API_HASH") or ""
    BOT_TOKEN = os.environ.get(f"{prefix}_TOKEN") or ""
    SESSION_FILE = Path(tempfile.gettempdir()) / (
        f"{name}-mtproto-" + hashlib.sha256(BOT_TOKEN.encode()).hexdigest()[:12] + ".session")
    CONFIGURED = _configured()
    return CONFIGURED


def usable(chat_id: int) -> bool:
    return CONFIGURED and chat_id > 0


def describe() -> str:
    if VERIFIED is False:
        return (f"files past the Bot API's limits are refused: Telegram refused the API id and hash "
                f"({PREFIX}_API_ID / {PREFIX}_API_HASH, or TELEGRAM_API_ID / TELEGRAM_API_HASH)")
    if CONFIGURED:
        return f"files past the Bot API's limits go over MTProto ({PREFIX}_API_ID or TELEGRAM_API_ID is set)"
    return (f"files past the Bot API's limits are refused (set {PREFIX}_API_ID and {PREFIX}_API_HASH, "
            f"or TELEGRAM_API_ID and TELEGRAM_API_HASH, to lift them)")


def on_disabled(hook) -> None:
    _on_disabled.append(hook)


async def verify() -> bool:
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
        if not _closing:
            logger.warning("Could not check the MTProto login for big files (%s: %s); the route stays on.",
                           type(exc).__name__, exc)
        return True
    VERIFIED = True
    logger.info("MTProto login for big files checked: Telegram accepted it.")
    return True


_resting_until = 0.0
_children: set = set()
_closing = False


def _child_environment() -> dict:
    env = {key: value for key, value in dict(os.environ).items() if isinstance(value, str)}
    env.update(BIG_FILES_API_ID=API_ID, BIG_FILES_API_HASH=API_HASH, BIG_FILES_TOKEN=BOT_TOKEN,
               BIG_FILES_SESSION=str(SESSION_FILE), PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    return env


TRANSFER_MEMORY_MB = 80
SMALL_SERVICE_MB = 2048


def transfers_at_once() -> int:
    fixed = os.environ.get("BIG_FILES_AT_ONCE")
    if fixed:
        return max(1, int(fixed))
    try:
        import resources
    except ImportError:
        return 2
    limit = resources.memory_limit_mb()
    return 1 if limit is not None and limit < SMALL_SERVICE_MB else 2


TRANSFERS_AT_ONCE = transfers_at_once()


def busy() -> bool:
    import resources
    return resources.shared_slots("big_files", TRANSFERS_AT_ONCE).locked()


async def _run(job: dict, progress=None, waiting=None) -> dict:
    import resources
    slots = resources.shared_slots("big_files", TRANSFERS_AT_ONCE)
    if slots.locked() and waiting is not None:
        try:
            result = waiting()
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            logger.debug("Could not say that a transfer is waiting", exc_info=True)
    async with slots:
        return await _run_one(job, progress)


async def _run_one(job: dict, progress=None) -> dict:
    global _resting_until
    if not CONFIGURED:
        raise Unavailable(f"{PREFIX or 'the bot'}'s API id and hash are not set")
    if time.monotonic() < _resting_until:
        raise Unavailable("resting after a failed connection")
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-u", *map(str, CHILD), json.dumps(job),
        cwd=str(SCRIPT.parent), env=_child_environment(),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _children.add(proc)
    outcome = None
    errors = bytearray()

    async def keep_stderr_tail():
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
                continue
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
                   progress=None, waiting=None) -> int:
    if not usable(chat_id):
        raise Unavailable("big files are only fetched in private chats")
    outcome = await _run({"op": "download", "chat_id": chat_id, "message_id": message_id,
                          "dest": str(dest), "expected_size": expected_size,
                          "limit_s": transfer_limit_s(expected_size)}, progress, waiting)
    return int(outcome.get("bytes") or 0)


async def send_document(chat_id: int, path, filename: str, caption: str = "", progress=None,
                        waiting=None, video: "dict | None" = None) -> None:
    if not usable(chat_id):
        raise Unavailable("big files are only sent in private chats")
    await _run({"op": "send", "chat_id": chat_id, "path": str(path), "filename": filename, "caption": caption,
                "limit_s": transfer_limit_s(Path(path).stat().st_size), "video": video}, progress, waiting)


async def close() -> None:
    global _closing
    _closing = True
    for proc in list(_children):
        try:
            if proc.returncode is None:
                proc.kill()
        except Exception:
            logger.debug("Could not stop a transfer process", exc_info=True)


def _emit(line: dict) -> None:
    sys.stdout.write(json.dumps(line) + "\n")
    sys.stdout.flush()


def _save_session(text: str) -> None:
    partial = SESSION_FILE.with_suffix(f".{os.getpid()}.partial")
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(partial, SESSION_FILE)


async def _connect():
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
            try:
                _save_session(client.session.save())
            except OSError:
                logger.warning("Could not keep the MTProto login in %s; the next transfer logs in again.",
                               SESSION_FILE, exc_info=True)
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
        message = await client.get_messages(None, ids=job["message_id"])
    except Exception as exc:
        raise Unavailable(f"{type(exc).__name__}: {exc}") from exc
    if message is None or getattr(message, "media", None) is None:
        raise NotThere("the message with the file is no longer in the chat")
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
    uploaded = await client.upload_file(job["path"], part_size_kb=PART_BYTES // 1024,
                                        file_name=job["filename"], progress_callback=progress)
    attributes = [types.DocumentAttributeFilename(job["filename"])]
    video = job.get("video") or None
    if video:
        attributes.append(types.DocumentAttributeVideo(
            duration=int(video.get("duration") or 0), w=int(video.get("width") or 0),
            h=int(video.get("height") or 0), supports_streaming=True))
    await client.send_file(
        types.PeerUser(job["chat_id"]), file=uploaded, caption=job.get("caption") or None,
        force_document=not video, supports_streaming=bool(video), parse_mode=None,
        attributes=attributes)


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
    API_ID = os.environ.get("BIG_FILES_API_ID", "")
    API_HASH = os.environ.get("BIG_FILES_API_HASH", "")
    BOT_TOKEN = os.environ.get("BIG_FILES_TOKEN", "")
    SESSION_FILE = Path(os.environ.get("BIG_FILES_SESSION") or SESSION_FILE)
    asyncio.run(_child(json.loads(sys.argv[1])))
