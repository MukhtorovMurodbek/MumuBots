#!/usr/bin/env python3
"""Compare the command menu each live bot is serving with the one in this code.

    python scripts/check_live_menus.py            every bot with a token in a .env
    python scripts/check_live_menus.py convert    one bot

Read-only: getMyCommands and nothing else, so it can run against production at
any time without disturbing it. Tokens come from each bot's own .env, or
shared/.env beneath it, and are never printed.

tests/test_menu.py reads the source and so can only say what the code will
publish. This asks Telegram what it is actually showing -- which differs when
a bot has not been redeployed since a menu changed, when a deploy failed to
publish, or when a menu left in a scope the code does not own outranks the
default (what once kept StickerBot's commands on TestBot's token after it ran
ConvertBot's code).
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOTS = {"sticker": ("sticker_bot", "SBOT"), "convert": ("convert_bot", "CBOT"),
        "downloader": ("downloader_bot", "DBOT"), "anon": ("anon_bot", "ABOT"),
        "manager": ("manager_bot", "MBOT")}
LANGUAGES = (None, "en", "uz", "ru")
UNOWNED_SCOPES = ("all_private_chats", "all_group_chats", "all_chat_administrators")


def env_of(folder: str) -> dict:
    """The bot's settings as it reads them itself: its own .env over
    shared/.env (shared/envfiles.py)."""
    if str(ROOT / "shared") not in sys.path:
        sys.path.insert(1, str(ROOT / "shared"))
    import envfiles
    return envfiles.merged(ROOT / folder)


def expected(folder: str, name: str = "BOT_COMMANDS") -> list[str]:
    source = (ROOT / folder / "bot.py").read_text(encoding="utf-8")
    block = re.search(name + r"\s*=\s*\[(.*?)\n\]", source, re.S)
    return re.findall(r'BotCommand\(\s*"([a-z_]+)"', block.group(1)) if block else []


def compare(label: str, served: list[str], want: list[str]) -> int:
    if served == want:
        print(f"  ok    {label}: {len(served)} commands, as in the code")
        return 0
    print(f"  DIFF  {label}: missing {[c for c in want if c not in served]}, "
          f"extra {[c for c in served if c not in want]}"
          + (", same commands in another order" if sorted(served) == sorted(want) else ""))
    return 1


def call(token: str, method: str, payload: dict):
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.loads(response.read())
    if not data.get("ok"):
        raise RuntimeError(data.get("description"))
    return data["result"]


def check(word: str) -> int:
    folder, prefix = BOTS[word]
    settings = env_of(folder)
    token = settings.get(f"{prefix}_TOKEN")
    if not token:
        print(f"{folder}: no {prefix}_TOKEN in its .env -- skipped")
        return 0
    me = call(token, "getMe", {})
    problems = 0
    if folder == "manager_bot":
        # Private: strangers get one line, the owner's own chat the whole menu.
        want = expected(folder)
        print(f"@{me['username']} ({folder}): the owner's menu has {len(want)} commands")
        problems += compare("strangers", [c["command"] for c in call(token, "getMyCommands", {})],
                            expected(folder, "STRANGER_COMMANDS"))
        admins = [a.strip() for a in (settings.get(f"{prefix}_ADMIN_ID") or settings.get("ADMIN_ID") or "").split(",")
                  if a.strip().isdigit()]
        for index, admin in enumerate(admins, 1):
            served = call(token, "getMyCommands", {"scope": {"type": "chat", "chat_id": int(admin)}})
            problems += compare(f"owner #{index}'s chat", [c["command"] for c in served], want)
        return problems
    want = expected(folder)
    print(f"@{me['username']} ({folder}): the code's menu has {len(want)} commands")
    for language in LANGUAGES:
        payload = {"language_code": language} if language else {}
        served = [c["command"] for c in call(token, "getMyCommands", payload)]
        label = language or "untagged"
        if language in ("uz", "ru") and not served:
            print(f"  -     {label}: nothing published (clients fall back to the untagged menu)")
            continue
        problems += compare(label, served, want)
    for scope in UNOWNED_SCOPES:
        leftover = call(token, "getMyCommands", {"scope": {"type": scope}})
        if leftover:
            problems += 1
            print(f"  DIFF  scope {scope} holds {len(leftover)} commands the code never set: it "
                  f"outranks the default menu in those chats")
    return problems


def main(argv: list[str]) -> int:
    words = argv or list(BOTS)
    unknown = [w for w in words if w not in BOTS]
    if unknown:
        raise SystemExit(f"Unknown bot(s): {unknown}. Known: {', '.join(BOTS)}")
    problems = 0
    for word in words:
        try:
            problems += check(word)
        except Exception as exc:
            problems += 1
            print(f"{word}: could not ask Telegram ({type(exc).__name__}: {exc})")
    print("\nEvery served menu matches the code." if not problems
          else f"\n{problems} difference(s). A redeploy publishes the code's menu on start.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
