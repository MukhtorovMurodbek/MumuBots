import groups

KIND = "channel"
PLATFORMS = groups.PLATFORMS
QUALITIES = groups.QUALITIES
LANGS = ("en", "uz", "ru")
TOGGLES = ("on", "as_file", "credit", "repost")

DEFAULTS = {
    "on": True,
    "platforms": list(PLATFORMS),
    "quality": 0,
    "as_file": False,
    "credit": True,
    "repost": True,
    "lang": "en",
}

CAPTION_MAX = 1024
ALBUM_MAX = 10

PREFIX = "cs:"


def normalize(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    settings = dict(DEFAULTS, platforms=list(PLATFORMS), kind=KIND)
    for key in TOGGLES:
        if isinstance(raw.get(key), bool):
            settings[key] = raw[key]
    if isinstance(raw.get("platforms"), list):
        settings["platforms"] = [p for p in PLATFORMS if p in raw["platforms"]]
    if raw.get("quality") in QUALITIES:
        settings["quality"] = raw["quality"]
    if raw.get("lang") in LANGS:
        settings["lang"] = raw["lang"]
    return settings


def is_channel(raw) -> bool:
    return isinstance(raw, dict) and raw.get("kind") == KIND


def apply(settings: dict, action: str, value: str = "") -> "dict | None":
    changed = dict(settings, platforms=list(settings["platforms"]))
    if action in TOGGLES:
        changed[action] = not settings[action]
    elif action == "p" and value in PLATFORMS:
        if value in changed["platforms"]:
            changed["platforms"].remove(value)
        else:
            changed["platforms"] = [p for p in PLATFORMS if p in changed["platforms"] or p == value]
    elif action == "q" and value.isdigit() and int(value) in QUALITIES:
        changed["quality"] = int(value)
    else:
        return None
    return changed


def data(chat_id: int, action: str, value: str = "") -> str:
    return f"{PREFIX}{int(chat_id)}:{action}" + (f":{value}" if value else "")


def parse(text: str) -> "tuple[int, str, str] | None":
    if not text.startswith(PREFIX):
        return None
    chat, _, rest = text[len(PREFIX):].partition(":")
    action, _, value = rest.partition(":")
    try:
        chat_id = int(chat)
    except ValueError:
        return None
    return (chat_id, action, value) if action else None


def utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def caption_for(text: str, credit: "str | None") -> str:
    if credit:
        joined = f"{text}\n\n{credit}" if text else credit
        if utf16_length(joined) <= CAPTION_MAX:
            return joined
    return text


def plan(items: int, caption: str, can_edit: bool, can_post: bool, can_delete: bool,
         repost: bool) -> "str | None":
    if items < 1 or utf16_length(caption) > CAPTION_MAX:
        return None
    if items == 1 and can_edit:
        return "edit"
    if repost and can_post and can_delete:
        return "repost"
    return None


def missing_rights(can_edit: bool, can_post: bool, can_delete: bool, repost: bool) -> list:
    missing = [] if can_edit else ["edit"]
    if repost:
        missing += [name for name, has in (("post", can_post), ("delete", can_delete)) if not has]
    return missing
