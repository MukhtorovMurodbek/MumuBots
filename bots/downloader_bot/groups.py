import platforms

PLATFORMS = ("instagram", "tiktok", "twitter", "pinterest", "reddit", platforms.UNADVERTISED)
QUALITIES = (0, 720, 480, 360)

DEFAULTS = {
    "on": True,
    "platforms": list(PLATFORMS),
    "quality": 0,
    "as_file": False,
    "credit": True,
    "admins_only": False,
}

PREFIX = "gs:"


def normalize(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    settings = dict(DEFAULTS)
    for key in ("on", "as_file", "credit", "admins_only"):
        if isinstance(raw.get(key), bool):
            settings[key] = raw[key]
    if isinstance(raw.get("platforms"), list):
        settings["platforms"] = [p for p in PLATFORMS if p in raw["platforms"]]
    if raw.get("quality") in QUALITIES:
        settings["quality"] = raw["quality"]
    return settings


def needs_admin(action: str, settings: dict) -> bool:
    return action == "admins" or settings["admins_only"]


def apply(settings: dict, action: str, value: str = "") -> "dict | None":
    changed = dict(settings, platforms=list(settings["platforms"]))
    if action in ("on", "as_file", "credit", "admins"):
        key = "admins_only" if action == "admins" else action
        changed[key] = not settings[key]
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


def parse(data: str) -> "tuple[str, str] | None":
    if not data.startswith(PREFIX):
        return None
    action, _, value = data[len(PREFIX):].partition(":")
    return (action, value) if action else None
