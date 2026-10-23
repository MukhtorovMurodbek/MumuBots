"""Runs each bot's main() with run_polling stubbed out.

That exercises everything except the network: imports, init_db against the
real Postgres, every handler registration, family_link.attach, the
maintenance jobs, and the polling arguments themselves.
"""
import os
import subprocess
import sys
from pathlib import Path

# The bots' own output is full of emoji (a /status starts with a chart, an
# error summary with a tick), and a Windows console still defaults to cp1252
# -- printing a child's reply there raised UnicodeEncodeError and took the
# whole run down before it reached the assertions. The child processes are
# already told to speak UTF-8; this is the same instruction for the runner's
# own stdout.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DSN = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/botfamily_test")

BOTS = {
    "sticker_bot":    ("sticker_bot", {"SBOT_TOKEN": "1:AA", "SBOT_USERNAME": "x", "SBOT_ADMIN_ID": "1"}),
    "convert_bot":    ("convert_bot", {"CBOT_TOKEN": "1:AA", "CBOT_USERNAME": "x", "CBOT_ADMIN_ID": "1"}),
    "downloader_bot": ("downloader_bot", {"DBOT_TOKEN": "1:AA", "DBOT_USERNAME": "x", "DBOT_ADMIN_ID": "1"}),
    "anon_bot":       ("anon_bot", {"ABOT_TOKEN": "1:AA", "ABOT_USERNAME": "x", "ABOT_ADMIN_ID": "1"}),
    "manager_bot":     ("manager_bot", {"MBOT_TOKEN": "1:AA", "MBOT_USERNAME": "x", "MBOT_ADMIN_ID": "1"}),
}

CHILD = r'''
import os, sys, logging
sys.path.insert(0, os.getcwd())
sys.path.insert(1, os.path.join(os.path.dirname(os.getcwd()), "shared"))

from telegram.ext import Application

captured = {}

def fake_run_polling(self, *args, **kwargs):
    captured["kwargs"] = kwargs
    captured["handlers"] = sum(len(v) for v in self.handlers.values())
    captured["groups"] = sorted(self.handlers)
    captured["jobs"] = [j.name for j in self.job_queue.jobs()] if self.job_queue else []
    captured["post_stop"] = self.post_stop is not None

Application.run_polling = fake_run_polling

import bot
bot.main()

assert captured, "run_polling was never reached"
kw = captured["kwargs"]
assert kw.get("timeout") == 30, kw
assert "message" in kw.get("allowed_updates", []), kw
print("  handlers:", captured["handlers"], "in groups", captured["groups"])
print("  allowed_updates:", kw["allowed_updates"], "| timeout:", kw["timeout"])
print("  jobs:", captured["jobs"])
print("  post_stop registered:", captured["post_stop"])

import db
db.close_pool()
'''


def main() -> int:
    failures = 0
    # Every child ends in family_link.attach(), whose key guard refuses a
    # database not stamped as sealed; this scratch one carries rows from other
    # suites, so it is stamped the way the conversion script leaves a real one.
    sys.path.insert(0, str(ROOT / "tests"))
    sys.path.insert(1, str(ROOT / "shared"))
    import seal_stamp
    seal_stamp.stamp_database(DSN)
    for name, (folder, extra) in BOTS.items():
        print(f"== {name} ==")
        env = dict(os.environ)
        env.update({
            "DATABASE_URL": DSN,
            "DB_SCHEMA": folder,
            "PYTHONIOENCODING": "utf-8",
            "SIBLING_BOTS": "stickerbot:StickerBot:a,convertbot:ConvertBot:b,"
                            "downloaderbot:DownloaderBot:c,anonbot:AnonBot:d",
        })
        env.update(extra)
        proc = subprocess.run(
            [sys.executable, "-c", CHILD], cwd=ROOT / folder, env=env,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        out = proc.stdout.rstrip()
        if out:
            print(out)
        if proc.returncode != 0:
            failures += 1
            print(proc.stderr.rstrip()[-3000:])
        else:
            # warnings still matter
            for line in proc.stderr.splitlines():
                if "Warning" in line or "warn" in line.lower():
                    print("  stderr:", line)
    print("\nFAILURES:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
