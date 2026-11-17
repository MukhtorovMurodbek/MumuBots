import json
import re
import zipfile
from io import BytesIO

from telegram import InputSticker

import i18n
from image_utils import to_sticker_png

TELEGRAM_PACK_RE = re.compile(
    r"(?:https?://)?t\.me/addstickers/([A-Za-z0-9_]+)|^([A-Za-z0-9_]{1,64})$"
)

MAX_IMPORT_PER_RUN = 100

_MAX_UNCOMPRESSED_ENTRY = 32 * 1024 * 1024


class ImportError_(Exception):
    pass


def parse_telegram_pack_source(text: str) -> str | None:
    text = text.strip()
    m = TELEGRAM_PACK_RE.match(text)
    if not m:
        return None
    return m.group(1) or m.group(2)


async def fetch_importable_stickers(bot, source_name: str, lang: str = "en"):
    try:
        sticker_set = await bot.get_sticker_set(source_name)
    except Exception as exc:
        raise ImportError_(i18n.t(lang, "import_pack_not_found", source=source_name)) from exc
    return sticker_set.stickers


def sticker_to_input_sticker(sticker) -> InputSticker | None:
    if sticker.is_animated:
        return None
    fmt = "video" if sticker.is_video else "static"
    emoji_list = [sticker.emoji] if sticker.emoji else ["\U0001F62D"]
    return InputSticker(sticker=sticker.file_id, emoji_list=emoji_list, format=fmt)


_IMG_EXT_RE = re.compile(r"\.(webp|png|jpg|jpeg)$", re.IGNORECASE)


def parse_whatsapp_zip(raw: bytes, lang: str = "en") -> list[tuple[bytes, list[str]]]:
    try:
        zf = zipfile.ZipFile(BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise ImportError_(i18n.t(lang, "import_bad_zip")) from exc

    emoji_by_filename: dict[str, list[str]] = {}
    for name in zf.namelist():
        if name.lower().endswith(("contents.json", "sticker_pack.json", "manifest.json")):
            try:
                data = json.loads(zf.read(name).decode("utf-8"))
                for entry in data.get("stickers", []):
                    img = entry.get("image_file")
                    emojis = entry.get("emojis") or entry.get("emoji")
                    if img and emojis:
                        emoji_by_filename[img] = list(emojis) if isinstance(emojis, list) else [emojis]
            except Exception:
                pass

    results: list[tuple[bytes, list[str]]] = []
    for info in sorted(zf.infolist(), key=lambda i: i.filename):
        if len(results) >= MAX_IMPORT_PER_RUN:
            break
        if not _IMG_EXT_RE.search(info.filename):
            continue
        if info.file_size > _MAX_UNCOMPRESSED_ENTRY:
            continue
        base = info.filename.rsplit("/", 1)[-1]
        try:
            png_bytes = to_sticker_png(zf.read(info)).getvalue()
        except Exception:
            continue
        emojis = emoji_by_filename.get(base) or emoji_by_filename.get(info.filename) or ["\U0001F62D"]
        results.append((png_bytes, emojis))

    zf.close()
    if not results:
        raise ImportError_(i18n.t(lang, "import_zip_no_images"))
    return results
