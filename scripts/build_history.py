#!/usr/bin/env python3
"""Build MumuBotsHistory: every version folder before 2.0.0, as it was, with
nothing in it that should not leave this laptop.

    python scripts/build_history.py                 # into %TEMP%\\MumuBotsHistory, files only
    python scripts/build_history.py --git           # and a repository, one commit per version
    python scripts/build_history.py --out D:\\x      # somewhere else
    python scripts/build_history.py --replace       # over an earlier build of this script

IT LIVES ON GITHUB ONLY. The owner, 2026-09-29: "MumuBotsHistory will only
be for github" -- a copy beside the version folders it was built from is the
same files twice. So it is built in the temp folder, pushed, and deleted; the
version folders stay the source, and this script makes it again, the same
commit for commit (checked 2026-09-29: all 35 trees, dates and messages).

The owner, 2026-09-28: two repositories from scratch -- "One will be
"MumuBotsHistory", with version history up until 2.0.0, having all the
folders in my disk by versions, and other "MumuBots", that will have the
further commit history starting from 2.0.0. Make sure nothing will have the
hardcoded token in it."

WHAT IS LEFT OUT, and why
    .git              each folder's copy of telegram-bot-family's history, whose
                      pre-0.8 commits hold tokens; the folders are its readable
                      form, and BotFamily1.7.0\\.git keeps it on this laptop
    .env, token.env   the real settings: every bot's token, the database password
    backups/          database dumps -- real people's data (the README stays)
    *.db, *.sqlite    0.1-0.3's SQLite files, and their -wal/-shm beside them: real
                      packs, anonymous inboxes, payments
    logs/, *.log      what the bots said about the people using them
    *.session         Telethon's logged-in session for a bot
    __pycache__/, venv/, tests/.mirror/   rebuilt, never written by hand
    .claude/          a coding assistant's own state -- 0.4.0 holds a whole
                      worktree of the repository in it

WHAT IS CHANGED
    Every Telegram bot token left in what is copied becomes
    <REDACTED-BOT-TOKEN>, every password in a postgresql:// address becomes
    <REDACTED>, and an API hash after API_HASH becomes <REDACTED>. 0.1.1's
    bot.py and 0.3.0's anon_bot and downloader_bot hard-code tokens that were
    still live on 2026-09-28.

THEN IT CHECKS ITS OWN WORK
    The patterns catch the shape of a secret. So every secret value in every
    .env on this laptop -- each version folder's, the C folder's, TestBot's
    token.env -- and every token the old sources hard-code is read (never
    printed), and the build fails if any one of them is anywhere in the
    output. That also catches a secret shaped in a way no pattern expected.

ONE COMMIT PER VERSION (--git), oldest first, dated when the version was
tagged -- or, for the versions before tags, by the folder's newest file -- so
`git log` reads as the folder list does. Never pushes: the push, and whether
the repository is private, are the owner's (docs/changes/CHANGES_v2.0.0.md,
"GitHub: 12 repositories become 3").
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PARENT = ROOT.parent
FOLDER = re.compile(r"^BotFamily(\d+)\.(\d+)\.(\d+)([A-Za-z]*)$")
FIRST_OF_MUMUBOTS = (2, 0, 0)
MARKER = ".mumubots-history"
# Beside the version folders, and part of "all the folders in my disk": the
# original logo drawings.
EXTRA_FOLDERS = ("Design",)

SKIP_DIRS = {".git", "__pycache__", "venv", ".venv", "logs", ".mirror", ".pytest_cache",
             "node_modules", ".idea", ".claude"}
SKIP_NAMES = {".env", "token.env", ".persona", ".lock", "release.log"}
SKIP_SUFFIXES = (".db", ".sqlite", ".sqlite3", "-journal", "-wal", "-shm", ".session",
                 ".log", ".pyc", ".pyo", ".dump")

TOKEN = re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")
# Documentation's made-up example, kept so the setup instructions still read.
EXAMPLE_TOKENS = {"8123456789:AAH0pQ7-abcdefGHIJklmnOPqrstUVwxyz0"}
DSN_PASSWORD = re.compile(r"(postgres(?:ql)?://[^:/\s\"'@<>]+:)([^@\s\"'<>]+)(@)")
PLACEHOLDER = re.compile(r"^(postgres|password|pass|p|secret\w*|stub|test|x|changeme|CHANGE-ME"
                         r"|YOUR[-_]PASSWORD|\*+)$|^[<\[{$%]", re.I)
API_HASH = re.compile(r"((?:API_HASH|api_hash)\s*[=:]\s*[\"']?)([0-9a-fA-F]{32})")
# Keys whose values are secrets; for DATABASE_URL, the password in it.
SECRET_KEY = re.compile(r"(TOKEN|API_HASH|PASSWORD|SECRET|PRIVATE|COOKIE|_KEY)$|^DATABASE_URL$")


def version_key(name: str):
    m = FOLDER.match(name)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4))


def version_folders() -> list[Path]:
    found = [(version_key(p.name), p) for p in PARENT.iterdir() if p.is_dir()]
    found = [(k, p) for k, p in found if k and k[:3] < FIRST_OF_MUMUBOTS]
    return [p for _k, p in sorted(found)]


def skipped(relative: Path, is_dir: bool) -> bool:
    name = relative.name
    if is_dir:
        return name in SKIP_DIRS
    if name in SKIP_NAMES or name.lower().endswith(SKIP_SUFFIXES):
        return True
    # A database dump is data whatever it is called; only the folder's README
    # says anything about it.
    return "backups" in relative.parts[:-1] and name.lower() != "readme.md"


def scrub(text: str) -> tuple[str, int]:
    count = 0

    def token(m):
        nonlocal count
        if m.group(0) in EXAMPLE_TOKENS:
            return m.group(0)
        count += 1
        return "<REDACTED-BOT-TOKEN>"

    def dsn(m):
        nonlocal count
        if PLACEHOLDER.search(m.group(2)):
            return m.group(0)
        count += 1
        return m.group(1) + "<REDACTED>" + m.group(3)

    def api_hash(m):
        nonlocal count
        count += 1
        return m.group(1) + "<REDACTED>"

    text = TOKEN.sub(token, text)
    text = DSN_PASSWORD.sub(dsn, text)
    text = API_HASH.sub(api_hash, text)
    return text, count


def copy_folder(source: Path, target: Path) -> dict:
    stats = {"files": 0, "redacted": 0, "left_out": 0}
    target.mkdir(parents=True, exist_ok=True)
    for dirpath, dirnames, filenames in os.walk(source):
        here = Path(dirpath)
        relative_dir = here.relative_to(source)
        kept = []
        for d in dirnames:
            if skipped(relative_dir / d, True):
                stats["left_out"] += 1
            else:
                kept.append(d)
        dirnames[:] = kept
        for name in filenames:
            relative = relative_dir / name
            if skipped(relative, False):
                stats["left_out"] += 1
                continue
            data = (here / name).read_bytes()
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                text = None
            if text is not None:
                text, count = scrub(text)
                stats["redacted"] += count
                data = text.encode("utf-8")
            out = target / relative
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(data)
            shutil.copystat(here / name, out)
            stats["files"] += 1
    if not stats["files"]:
        # Still a version on the list, and still a commit: say why it is empty.
        (target / "EMPTY.md").write_text(
            f"Everything in {source.name} was left out -- settings or data, see the README "
            f"at the top of this repository.\n", encoding="utf-8")
    return stats


def env_values(path: Path) -> dict:
    values = {}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if value:
            values[key] = value
    return values


def known_secrets() -> dict:
    """{secret value: where it came from}, from every settings file on this
    laptop and every token the old sources hard-code. The values are only
    ever compared, never shown."""
    secrets = {}
    for folder in sorted(p for p in PARENT.iterdir() if p.is_dir() and version_key(p.name)):
        for dirpath, dirnames, filenames in os.walk(folder):
            dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__", "node_modules",
                                                             "venv", ".venv", ".mirror")]
            for name in filenames:
                path = Path(dirpath) / name
                where = str(path.relative_to(PARENT))
                if name.endswith(".env") and not name.endswith(".example"):
                    for key, value in env_values(path).items():
                        if not SECRET_KEY.search(key):
                            continue
                        if key == "DATABASE_URL":
                            m = DSN_PASSWORD.search(value)
                            if not m or PLACEHOLDER.search(m.group(2)):
                                continue
                            value = m.group(2)
                        if len(value) >= 8:
                            secrets.setdefault(value, f"{where}: {key}")
                elif name.endswith((".py", ".md", ".txt", ".ps1", ".toml", ".json", ".example",
                                    ".yml", ".yaml", ".cfg", ".ini", ".sh", ".cmd")):
                    try:
                        text = path.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    for m in TOKEN.finditer(text):
                        if m.group(0) not in EXAMPLE_TOKENS:
                            secrets.setdefault(m.group(0), f"{where}: a hard-coded token")
    return secrets


def leaks(out: Path, secrets: dict) -> list[str]:
    """Every (file, source) where a known secret, or anything shaped like a
    token, survived into the output."""
    needles = [(value.encode("utf-8"), where) for value, where in secrets.items()]
    found = []
    for dirpath, dirnames, filenames in os.walk(out):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in filenames:
            path = Path(dirpath) / name
            data = path.read_bytes()
            for needle, where in needles:
                if needle in data:
                    found.append(f"{path.relative_to(out)} holds the secret from {where}")
            for m in TOKEN.finditer(data.decode("utf-8", errors="replace")):
                if m.group(0) not in EXAMPLE_TOKENS:
                    found.append(f"{path.relative_to(out)} holds something shaped like a bot token")
    return found


def tag_dates() -> dict:
    """{'BotFamily1.2.0': iso date} from telegram-bot-family's tags."""
    for holder in (ROOT, PARENT / "BotFamily1.7.0"):
        if (holder / ".git").exists():
            result = subprocess.run(
                ["git", "for-each-ref", "refs/tags", "--format=%(refname:short) %(creatordate:iso-strict)"],
                cwd=holder, capture_output=True, text=True, encoding="utf-8", errors="replace")
            dates = {}
            for line in result.stdout.splitlines():
                tag, _, when = line.partition(" ")
                if tag.startswith("v") and when:
                    dates[f"BotFamily{tag[1:]}"] = when
            if dates:
                return dates
    return {}


def newest_file(folder: Path) -> str | None:
    """When the newest file in `folder` was written, or None if it has none."""
    newest = max((p.stat().st_mtime for p in folder.rglob("*") if p.is_file()), default=None) \
        if folder.is_dir() else None
    if newest is None:
        return None
    return dt.datetime.fromtimestamp(newest).astimezone().isoformat(timespec="seconds")


def headline(folder: Path) -> str:
    """What the version was, from the first heading of its own changes file --
    not the others an older folder carried along -- or nothing."""
    version = folder.name.removeprefix("BotFamily")
    for changes in folder.glob(f"**/CHANGES_v{version}.md"):
        if {".git", ".claude"} & set(changes.relative_to(folder).parts):
            continue
        for line in changes.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                title = re.sub(rf"^(Full change log\s*[—-]\s*)?v?{re.escape(version)}\s*[—-]?\s*", "", title)
                return title
    return ""


def _remove(out: Path) -> None:
    """rmtree, including git's read-only object files, which Windows will not
    delete until they are made writable."""
    def writable_then_retry(func, path, _exc):
        os.chmod(path, stat.S_IWRITE)
        func(path)
    if sys.version_info >= (3, 12):
        shutil.rmtree(out, onexc=writable_then_retry)
    else:
        shutil.rmtree(out, onerror=writable_then_retry)


def write_readme(out: Path, rows: list[tuple[str, str, str]]) -> None:
    lines = [
        "# MumuBotsHistory",
        "",
        "Every version of the Mumu bot family before 2.0.0, one folder each, as it",
        "was on the owner's laptop -- the way the owner has always read the version",
        "list. From 2.0.0 the bots live in [MumuBots](https://github.com/MukhtorovMurodbek/MumuBots),",
        "and the one-process edition in",
        "[MumuCombined](https://github.com/MukhtorovMurodbek/MumuCombined).",
        "",
        "Made by `scripts/build_history.py` in MumuBots. Left out of every folder:",
        "the settings files (`.env`), database dumps, the SQLite files of 0.1-0.3,",
        "logs, and each folder's own git history. Every bot token still in the",
        "code reads `<REDACTED-BOT-TOKEN>`: none of these folders can run as it is,",
        "and none of them holds anybody's data.",
        "",
        "Links in these folders to the family's older repositories -- StickerBot,",
        "ConvertBot, DownloaderBot, AnonBot, ParentBot, telegram-bot-family and the",
        "per-bot ones before them -- point at repositories that were retired in",
        "2.0.0. Nothing was lost with them: what they held is in these folders, and",
        "every file of the last release of each is in MumuBots.",
        "",
        "| folder | tagged or last changed | what it was |",
        "|---|---|---|",
    ]
    lines += [f"| `{name}` | {when[:10] or '?'} | {what} |" for name, when, what in rows]
    (out / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def git(out: Path, *args, env=None):
    result = subprocess.run(["git", *args], cwd=out, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", env=env)
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path(tempfile.gettempdir()) / "MumuBotsHistory")
    parser.add_argument("--git", action="store_true", help="make the repository, one commit per version")
    parser.add_argument("--replace", action="store_true", help="delete an earlier build of this script first")
    opts = parser.parse_args()
    out = opts.out.resolve()

    if out.exists() and any(out.iterdir()):
        if not (opts.replace and (out / MARKER).exists()):
            print(f"{out} is not empty. --replace deletes it first, and only if this script made it.")
            return 1
        _remove(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / MARKER).write_text("made by BotFamily2.0.0/scripts/build_history.py\n", encoding="utf-8")

    folders = version_folders()
    dates = tag_dates()
    rows, total = [], {"files": 0, "redacted": 0, "left_out": 0}
    for folder in folders:
        stats = copy_folder(folder, out / folder.name)
        for key in total:
            total[key] += stats[key]
        # Untagged, the newest file that was copied: what is left out can be
        # newer than the version (a database's index, touched by reading it).
        rows.append((folder.name, dates.get(folder.name) or newest_file(out / folder.name) or "",
                     headline(folder)))
        print(f"  {folder.name:<18} {stats['files']:>5} files, {stats['redacted']} redacted, "
              f"{stats['left_out']} left out")
    for extra in EXTRA_FOLDERS:
        if (PARENT / extra).is_dir():
            copy_folder(PARENT / extra, out / extra)
    write_readme(out, rows)
    print(f"{len(folders)} versions: {total['files']} files, {total['redacted']} secrets redacted, "
          f"{total['left_out']} files and folders left out.")

    secrets = known_secrets()
    found = leaks(out, secrets)
    if found:
        print(f"\nNOT SAFE -- {len(found)} leak(s); nothing may be pushed from here:")
        for line in found[:40]:
            print("  " + line)
        return 2
    print(f"Checked against {len(secrets)} secrets from this laptop's settings and old sources: none present.")

    if opts.git:
        git(out, "init", "-q", "-b", "main")
        git(out, "add", MARKER, "README.md", *[e for e in EXTRA_FOLDERS if (out / e).exists()])
        def dated(when):
            return dict(os.environ, **({"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when} if when else {}))
        first = next((when for _name, when, _what in rows if when), "")
        git(out, "commit", "-q", "-m", "MumuBotsHistory: what is here, and what is not", env=dated(first))
        for name, when, what in rows:
            env = dated(when)
            git(out, "add", "--", name)
            git(out, "commit", "-q", "--allow-empty", "-m", name + (f"\n\n{what}" if what else ""), env=env)
        print(f"Repository made: {len(rows) + 1} commits on main, in {out}. Nothing was pushed; "
              "after the push, delete it -- it lives on GitHub only.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
