"""End-to-end checks that need more than a database round trip:

  * the family command queue, driven the way ManagerBot drives it
  * the buffered activity tracker and the idle-user-data sweeper
  * the language keyboard's callback_data matching each bot's handler pattern
"""
import os
import re
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

QUEUE_TEST = r'''
import asyncio, os, sys
from datetime import datetime, timezone
sys.path.insert(0, os.getcwd())
sys.path.insert(1, os.path.join(os.path.dirname(os.getcwd()), "shared"))

import db, family_link

family_link.init_family_schema()
family_link._bot_id = "stickerbot"
family_link._display_name = "StickerBot"
family_link._start_time = datetime.now(timezone.utc)
family_link._enabled = True

class FakeBot:
    async def get_chat(self, user_id):
        raise RuntimeError("no network in this test")
    async def send_message(self, **kw):
        sent.append(kw); return None

class FakeContext:
    bot = FakeBot()

sent = []

# ManagerBot's side of the queue
with db.pooled() as conn:
    conn.execute("DELETE FROM family.commands WHERE target_bot = 'stickerbot'")
    conn.commit()

def queue(command, args=""):
    with db.pooled() as conn:
        cur = conn.execute(
            "INSERT INTO family.commands (target_bot, command, args, requested_by, reply_chat_id) "
            "VALUES ('stickerbot', %s, %s, 1, 1) RETURNING id", (command, args))
        cid = cur.fetchone()[0]; conn.commit(); return cid

def result(cid):
    with db.pooled() as conn:
        return conn.execute(
            "SELECT status, ok, output, file_name, length(file_bytes) FROM family.commands WHERE id = %s",
            (cid,)).fetchone()

async def run():
    for cmd, args in [("ping",""), ("status",""), ("errors",""), ("users","3"),
                      ("dbdump",""), ("logs","5"), ("whois","abc"), ("message","1"),
                      ("nosuchcommand","")]:
        cid = queue(cmd, args)
        await family_link._poll_commands(FakeContext())
        status, ok, output, fname, flen = result(cid)
        head = (output or "").splitlines()[0][:70]
        print(f"  {cmd:<14} -> {status:<7} ok={ok} {head}" + (f" [{fname}, {flen}B]" if fname else ""))
        assert status in ("done", "failed"), (cmd, status)
        if cmd == "nosuchcommand":
            assert ok is False and "Unknown command" in output, output
        else:
            assert ok is True, (cmd, output)
    # nothing left pending
    with db.pooled() as conn:
        left = conn.execute(
            "SELECT count(*) FROM family.commands WHERE target_bot='stickerbot' AND status='pending'"
        ).fetchone()[0]
    assert left == 0, left
    print("  queue drained")

asyncio.run(run())
db.close_pool()
'''

BUFFER_TEST = r'''
import asyncio, os, sys, time
from datetime import datetime, timezone
sys.path.insert(0, os.getcwd())
sys.path.insert(1, os.path.join(os.path.dirname(os.getcwd()), "shared"))

import db, shared_features as sf

class FakeUser:
    def __init__(self, uid): self.id = uid

class FakeUpdate:
    def __init__(self, uid): self.effective_user = FakeUser(uid)

class FakeUserData(dict): pass

class FakeContext:
    def __init__(self, store): self.user_data = store

async def run():
    store = {}
    for uid in (1, 2, 1, 3, 2):
        ctx = FakeContext(store.setdefault(uid, {}))
        await sf.track_activity(FakeUpdate(uid), ctx)
    assert sf._activity_buffer == {1, 2, 3}, sf._activity_buffer
    assert all("_last_seen" in d for d in store.values())
    print("  buffered 5 updates from 3 users ->", sorted(sf._activity_buffer))

    n = await asyncio.to_thread(sf._flush_activity_now)
    assert n == 3 and not sf._activity_buffer, (n, sf._activity_buffer)
    print("  flushed", n, "rows; buffer now empty")

    # a flush that fails must put the ids back, not lose them
    real = db.record_activity_batch
    db.record_activity_batch = lambda ids: (_ for _ in ()).throw(RuntimeError("db down"))
    sf._activity_buffer.update({7, 8})
    try:
        await asyncio.to_thread(sf._flush_activity_now)
    except RuntimeError:
        pass
    assert sf._activity_buffer == {7, 8}, sf._activity_buffer
    print("  failed flush kept the ids:", sorted(sf._activity_buffer))
    db.record_activity_batch = real
    await asyncio.to_thread(sf._flush_activity_now)

    # the idle-user sweeper
    class FakeApp:
        def __init__(self): self.user_data = store; self.dropped = []
        def drop_user_data(self, uid): self.dropped.append(uid); self.user_data.pop(uid, None)
    app = FakeApp()
    assert sf._prune_user_data(app) == 0, app.dropped
    store[1]["_last_seen"] = time.time() - (sf.USER_DATA_TTL_HOURS + 1) * 3600
    assert sf._prune_user_data(app) == 1 and app.dropped == [1], app.dropped
    assert 1 not in store and 2 in store
    print("  idle sweeper dropped", app.dropped, "kept", sorted(store))

asyncio.run(run())
db.close_pool()
'''


def run_child(folder: str, code: str, schema: str) -> int:
    env = dict(os.environ)
    env.update({"DATABASE_URL": DSN, "DB_SCHEMA": schema, "PYTHONIOENCODING": "utf-8"})
    proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT / folder, env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(proc.stdout.rstrip())
    if proc.returncode:
        print(proc.stderr.rstrip()[-2500:])
    return proc.returncode


def check_language_patterns() -> int:
    """The keyboard's callback_data must match the pattern each bot registers,
    and /language -- which is the only way to reach that keyboard now that
    /start prints the instructions instead -- must actually be wired up.
    tests/start_flow.py proves the handler behaves; this proves PTB will ever
    call it."""
    sf = (ROOT / "shared" / "shared_features.py").read_text(encoding="utf-8")
    m = re.search(r'callback_data=f"setlang:\{code\}"', sf)
    assert m, "language_keyboard no longer emits setlang:<code>"
    bad = 0
    for folder in ("sticker_bot", "convert_bot", "downloader_bot", "anon_bot"):
        src = (ROOT / folder / "bot.py").read_text(encoding="utf-8")
        if not re.search(r'CallbackQueryHandler\(language_chosen, pattern=r?"\^setlang:"\)', src):
            print(f"  !! {folder}: no handler registered for ^setlang:")
            bad += 1
            continue
        for code in ("en", "uz", "ru"):
            assert re.match(r"\^setlang:", "^setlang:") and f"setlang:{code}"
        if not re.search(r'CommandHandler\("language", language_command\)', src):
            print(f"  !! {folder}: /language is defined but never registered")
            bad += 1
            continue
        print(f"  {folder}: setlang handler + /language registered")
    return bad


def main() -> int:
    failures = 0
    print("== family command queue (as StickerBot) ==")
    failures += run_child("sticker_bot", QUEUE_TEST, "sticker_bot")
    print("== activity buffer + idle sweeper (as ConvertBot) ==")
    failures += run_child("convert_bot", BUFFER_TEST, "convert_bot")
    print("== language picker wiring ==")
    failures += check_language_patterns()
    print("\nFAILURES:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
