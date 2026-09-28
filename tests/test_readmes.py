"""Every command a public bot puts in its menu is in its README.

    python3 tests/test_readmes.py

The READMEs are what the public repositories show, and in 2.0.0 four of them
turned out to be missing commands the bots had been offering for versions:
/balance and /paysupport everywhere, DownloaderBot's /settings and AnonBot's
/export. Reads the source, not a bot: BOT_COMMANDS and ADMIN_COMMANDS in each
bot.py, against every `/command` the README mentions.
"""
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
PUBLIC = ["sticker_bot", "convert_bot", "downloader_bot", "anon_bot"]
failures = []


def commands(source: str, name: str) -> set:
    block = re.search(name + r"\s*=\s*\[(.*?)\n\]", source, re.S)
    return set(re.findall(r'BotCommand\(\s*"([a-z_]+)"', block.group(1))) if block else set()


for bot in PUBLIC:
    source = (REPO / bot / "bot.py").read_text(encoding="utf-8")
    offered = commands(source, "BOT_COMMANDS") | commands(source, "ADMIN_COMMANDS")
    readme = (REPO / bot / "README.md").read_text(encoding="utf-8")
    mentioned = set(re.findall(r"`/([a-z_]+)", readme))
    missing = sorted(offered - mentioned)
    ok = bool(offered) and not missing
    print(("  ok  " if ok else "  FAIL  ") + f"{bot}: all {len(offered)} menu commands are in its README"
          + ("" if ok else f"  -- missing {missing}"))
    if not ok:
        failures.append(bot)

sys.exit(1 if failures else 0)
