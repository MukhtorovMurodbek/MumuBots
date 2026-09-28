"""Every layer of a bot's name derives from one canonical id.

There are six places a bot is named -- the family name, the bus id, the
folder, the Python schema, the environment prefix and the Telegram @username
-- and until v1.3.0 the only thing keeping them in step was that whoever
added a bot copied the last one carefully. This asserts the rule instead, so
a sixth bot cannot be added half-named. (Until 2.0.0 there was a seventh, the
bot's own GitHub repository; since then the bots are folders of one, and the
deploy files in each folder are what carry its name to Railway.)

The rule, in full, is in ARCHITECTURE.md. In short: everything is mechanical
from `<Role>Bot` except the @username, which is an address rather than a
label and is therefore allowed to disagree -- but only by being listed as an
alias, so ManagerBot can still resolve it.

Reads source rather than running anything, like test_pause_gate.py: no
Postgres, no network, and it is part of `python tests/run.py`.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# name          folder            env prefix
FAMILY = [
    ("StickerBot",    "sticker_bot",    "SBOT"),
    ("ConvertBot",    "convert_bot",    "CBOT"),
    ("DownloaderBot", "downloader_bot", "DBOT"),
    ("AnonBot",       "anon_bot",       "ABOT"),
    ("ManagerBot",    "manager_bot",    "MBOT"),
]


def _snake(name: str) -> str:
    """StickerBot -> sticker_bot"""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _bus_id(name: str) -> str:
    """StickerBot -> stickerbot"""
    return name.lower()


failures = []


def check(label, ok, detail=""):
    print(("  ok   " if ok else "  FAIL ") + label + ("" if ok else "  [" + str(detail) + "]"))
    if not ok:
        failures.append(label)


def main() -> int:
    for name, folder, prefix in FAMILY:
        check(f"{name}: folder is the snake_case of the name",
              folder == _snake(name), f"{folder} != {_snake(name)}")

        source = (ROOT / folder / "bot.py").read_text(encoding="utf-8")

        # BOT_NAME is the bus id, and the bus id is the name lowercased.
        m = re.search(r'^BOT_NAME = "([a-z]+)"', source, re.M)
        check(f"{name}: BOT_NAME is set", m is not None, folder)
        if m:
            check(f"{name}: bus id is the name lowercased",
                  m.group(1) == _bus_id(name), f"{m.group(1)} != {_bus_id(name)}")

        # The env prefix is used for the token, the username and the admin id,
        # and nothing else in the family may use a different one.
        for suffix in ("TOKEN", "USERNAME", "ADMIN_ID"):
            check(f"{name}: reads {prefix}_{suffix}",
                  f'"{prefix}_{suffix}"' in source, folder)

        # The display name handed to the family bus is the family name --
        # written inline in the four child bots and through DISPLAY_NAME in
        # ManagerBot, which is the same fact either way.
        attaches = (f'family_link.attach(app, BOT_NAME, "{name}"' in source
                    or (f'DISPLAY_NAME = "{name}"' in source
                        and "family_link.attach(app, BOT_NAME, DISPLAY_NAME" in source))
        check(f"{name}: introduces itself to the bus as {name}", attaches, folder)

    # The deploy files are the other end of the same table: folder -> the
    # Railway service that builds it. The five pairs are copies of each other,
    # and a slip in one -- convert_bot/railway.json naming sticker_bot's
    # Dockerfile -- builds one bot under another's service, with the other's
    # variables, its token among them.
    deploys = {}
    for name, folder, _prefix in FAMILY:
        config = json.loads((ROOT / folder / "railway.json").read_text(encoding="utf-8"))
        build = config.get("build", {})
        check(f"{name}: railway.json builds {folder}/Dockerfile",
              build.get("dockerfilePath") == f"{folder}/Dockerfile", build.get("dockerfilePath"))
        check(f"{name}: rebuilds when {folder}/ or shared/ changes, and for nothing else",
              sorted(build.get("watchPatterns", [])) == sorted([f"/{folder}/**", "/shared/**"]),
              build.get("watchPatterns"))
        docker = (ROOT / folder / "Dockerfile").read_text(encoding="utf-8")
        strays = [other for _n, other, _p in FAMILY if other != folder and other in docker]
        check(f"{name}: its Dockerfile copies and runs {folder}, and no other bot",
              f"COPY {folder} {folder}" in docker and f"WORKDIR /app/{folder}" in docker
              and not strays, strays)
        deploys[folder] = config.get("deploy", {})
    check("every bot deploys with the same overlap, drain and restarts",
          len({json.dumps(d, sort_keys=True) for d in deploys.values()}) == 1, deploys)
    # shared/lifecycle.py's lease wait has to outlast the whole handover, or a
    # new container starts polling beside the old one: 409 Conflict, and the
    # updates split between them.
    lifecycle = (ROOT / "shared" / "lifecycle.py").read_text(encoding="utf-8")
    m = re.search(r'"DEPLOY_LEASE_WAIT_SECONDS", "(\d+)"', lifecycle)
    wait = int(m.group(1)) if m else 0
    handover = max(d.get("overlapSeconds", 0) + d.get("drainingSeconds", 0) for d in deploys.values())
    check("the deploy lease wait outlasts overlap plus drain",
          wait > handover, f"{wait}s wait, {handover}s handover")

    # Every Telegram username that does not match its bot's name has to be
    # listed as an alias, or ManagerBot cannot resolve what the owner types.
    manager = (ROOT / "manager_bot" / "bot.py").read_text(encoding="utf-8")
    block = manager.split("USERNAME_ALIASES = {", 1)[1].split("}", 1)[0]
    for name, _folder, _prefix in FAMILY:
        bus = _bus_id(name)
        check(f"{name}: has an alias entry", f'"{bus}":' in block, bus)

    print("")
    print("FAILURES: " + str(len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
