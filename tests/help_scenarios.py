"""/help in two lengths, in all four public bots, driven end to end.

    python tests/help_scenarios.py [sticker_bot convert_bot ...]

The owner, 2026-10-03: "Yes all bots should be similar or share a similar
template." Each bot runs its real main() on tests/sim.py's in-process
Telegram, in a process of its own (all four have modules called bot, db and
i18n), and is asked what a person would ask: /start, /help, the button under
it both ways, /help full. What comes back has to be the bot's own short and
full text, with the same buttons in every bot, and nothing may crash -- a
pattern that does not match or a group that swallows the tap shows up here
and nowhere else.

Needs a Postgres (TEST_DATABASE_URL, or the local default). No network, and
no .env read (ENV_FILES=off): the bots' own files hold real tokens.
"""
import os
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DSN = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/botfamily_test")
PREFIX = {"sticker_bot": "SBOT", "convert_bot": "CBOT", "downloader_bot": "DBOT", "anon_bot": "ABOT"}

CHILD = r'''
import asyncio, os, sys
from pathlib import Path
ROOT = Path(os.environ["HELP_ROOT"])
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, os.getcwd())
sys.path.insert(1, str(ROOT / "shared"))
import sim, db, i18n, shared_features
import bot as subject

failures, checks = [], 0
def check(label, ok, detail=""):
    global checks
    checks += 1
    print(("  ok   " if ok else "  FAIL ") + label + ("" if ok else f"\n       {detail}"))
    if not ok:
        failures.append(label)

crashes = []
async def main():
    db.init_db()
    # A brand-new person each run: their language row gone, whichever table
    # this bot keeps it in (DownloaderBot's is "settings").
    with db.pooled() as conn:
        table = conn.execute("SELECT CASE WHEN to_regclass('user_settings') IS NULL "
                             "THEN 'settings' ELSE 'user_settings' END").fetchone()[0]
        conn.execute(f"DELETE FROM {table} WHERE user_id = 7001")
        conn.commit()
    tele = sim.FakeTelegram(bot_username="helptest_bot")
    s, _ = await sim.build(subject, tele)
    async def record(update, context):
        crashes.append(f"{type(context.error).__name__}: {context.error}")
    s.app.add_error_handler(record)
    person = s.person(7001, "Person")
    await person.send("/start")
    await person.tap("English")
    brief, full = subject.build_help_text("en"), subject.build_help_text("en", full=True)
    full_label, brief_label = i18n.t("en", "help_full_button"), i18n.t("en", "help_brief_button")
    check("picking a language ends at the short help",
          any(brief in (m.get("text") or "") for m in person.chat), person.transcript())

    await person.send("/start")
    start = person.chat[-1]
    check("/start shows the short help", brief in (start.get("text") or ""), (start.get("text") or "")[:200])
    check("with the full-guide button first", (person.buttons(start["message_id"]) or [""])[0] == full_label,
          person.buttons(start["message_id"]))

    await person.send("/help")
    message = person.chat[-1]
    check("/help is the short help", message.get("text") == brief, (message.get("text") or "")[:200])
    check("with the full-guide button first", (person.buttons(message["message_id"]) or [""])[0] == full_label,
          person.buttons(message["message_id"]))
    await person.tap(full_label, message["message_id"])
    now = tele.find(person.id, message["message_id"]) or {}
    check("the button turns it into the full guide, in place", now.get("text") == full,
          (now.get("text") or "")[:200])
    check("whose first button goes back", (person.buttons(message["message_id"]) or [""])[0] == brief_label,
          person.buttons(message["message_id"]))
    await person.tap(brief_label, message["message_id"])
    check("and back", (tele.find(person.id, message["message_id"]) or {}).get("text") == brief)
    await person.send("/help full")
    check("/help full goes straight to the full guide", person.chat[-1].get("text") == full,
          (person.chat[-1].get("text") or "")[:200])
    check("the short help points at /feedback, and says it is one-way",
          "/feedback" in brief and "can't reply" in brief)
    check("nothing crashed", not crashes, " | ".join(crashes))
    print(f"{checks} checks, FAILURES: {len(failures)}")
    db.close_pool()
    return 1 if failures else 0

raise SystemExit(asyncio.run(main()))
'''


def main() -> int:
    bots = sys.argv[1:] or list(PREFIX)
    failed = []
    for bot in bots:
        print(f"\n== {bot} ==", flush=True)
        prefix = PREFIX[bot]
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1", ENV_FILES="off",
                   HELP_ROOT=str(ROOT), DATABASE_URL=DSN, DB_SCHEMA=f"{bot}_helpsim",
                   DEPLOY_SAFETY="off", FLOOD_UPDATES_PER_MINUTE="0", SIBLING_BOTS="",
                   FAMILY_BUS="off", **{f"{prefix}_TOKEN": "1:AA", f"{prefix}_USERNAME": "helptest_bot",
                                        f"{prefix}_ADMIN_ID": "1"})
        result = subprocess.run([sys.executable, "-c", CHILD], cwd=ROOT / bot, env=env)
        if result.returncode:
            failed.append(bot)
    print("\nFAILED: " + ", ".join(failed) if failed else "\nevery bot's help is the same template")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
