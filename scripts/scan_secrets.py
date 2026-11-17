#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FORBIDDEN_FILES = re.compile(r"(^|/)(\.env(\.[^/]*)?|[^/]*\.key|data_key|mumu_data_key|cookies[^/]*\.txt|[^/]*\.session)$")
ALLOWED_FILES = {".env.example"}

FINDINGS = (
    ("a bot token", re.compile(r"(?<![0-9])\d{6,12}:AA[A-Za-z0-9_-]{30,}")),
    ("an age private key", re.compile(r"AGE-SECRET-KEY-1[0-9A-Z]{50,}")),
    ("a private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("a cookie line", re.compile(r"^\.?[a-z0-9.-]+\.[a-z]{2,}\t(TRUE|FALSE)\t/\t(TRUE|FALSE)\t\d+\t\S+\t\S{16,}", re.M)),
)
DSN = re.compile(r"postgres(?:ql)?://([^:/\s\"'@<>]+):([^@\s\"'<>]+)@([A-Za-z0-9.\-]+)")
PLACEHOLDER_PASSWORD = re.compile(r"^(postgres|password|pass|pw|p|secret\w*|stub|test|x+|changeme|"
                                  r"CHANGE-ME|YOUR[-_]PASSWORD|\*+|\[.*\]|\{.*\}|\$\w+)$", re.I)
PLACEHOLDER_HOST = re.compile(r"(^|\.)(example|invalid|test|localhost|local)$|^(host|db|localhost|127\.0\.0\.1)$", re.I)
EXAMPLE_TOKENS = {"8123456789:AA"}


def _tracked(staged: bool) -> "list[str]":
    command = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"] if staged else ["git", "ls-files"]
    out = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line]


def _text(path: str, staged: bool) -> "str | None":
    try:
        if staged:
            data = subprocess.run(["git", "show", f":{path}"], cwd=ROOT, capture_output=True, check=True).stdout
        else:
            data = (ROOT / path).read_bytes()
    except (OSError, subprocess.CalledProcessError):
        return None
    if b"\0" in data[:8192]:
        return None
    return data.decode("utf-8", "replace")


def scan(paths, staged: bool = False) -> "list[str]":
    found = []
    for path in paths:
        if path not in ALLOWED_FILES and FORBIDDEN_FILES.search(path):
            found.append(f"{path}: a file of a kind that is never committed")
            continue
        text = _text(path, staged)
        if text is None:
            continue
        for line_number, line in enumerate(text.splitlines(), 1):
            for what, pattern in FINDINGS:
                match = pattern.search(line)
                if match and not any(match.group(0).startswith(example) for example in EXAMPLE_TOKENS):
                    found.append(f"{path}:{line_number}: {what}")
            for match in DSN.finditer(line):
                password, host = match.group(2), match.group(3)
                if not PLACEHOLDER_PASSWORD.match(password) and not PLACEHOLDER_HOST.search(host):
                    found.append(f"{path}:{line_number}: a password in a database address")
    return found


def install() -> int:
    hook = ROOT / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\nexec python3 scripts/scan_secrets.py --staged\n", encoding="utf-8")
    hook.chmod(0o755)
    print(f"Installed {hook.relative_to(ROOT)}: every commit in this clone is scanned first.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Refuse a commit or a CI run that would publish a secret.")
    parser.add_argument("--staged", action="store_true", help="scan what is staged for the next commit")
    parser.add_argument("--install", action="store_true", help="install the pre-commit hook in this clone")
    args = parser.parse_args(argv)
    if args.install:
        return install()
    found = scan(_tracked(args.staged), args.staged)
    for line in found:
        print(line, file=sys.stderr)
    if found:
        print(f"\n{len(found)} possible secret(s): nothing was committed. Remove them, or move the value "
              f"into a setting (.env, the host's variables).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
