"""Start the bots against the fake Bot API, send a crowd, print the memory over time.

    python membench/fake_telegram.py 8099 &           # the fake Bot API, in another terminal
    PEOPLE=2000 python membench/measure.py one        # main.py, all five bots in one process
    python membench/measure.py separate               # main.py, a process per bot
    python membench/measure.py rust                   # rust/mumu-core-poc (cargo build --release first)

Each of PEOPLE people sends /start and /help (REPEAT times) to every bot at once. The memory
is the resident set of the process and its children, sampled while idle, under the crowd, and
once every reply has gone out. A scratch database `mumu_ramtest` is made fresh on the Postgres
of BENCH_ADMIN_URL. TYPES_ON_SIGNAL=<file> writes the live objects by type at the end.
Restart fake_telegram.py between runs: it keeps what it was sent.
"""
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request

import psycopg

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("MUMU_REPO") or os.path.dirname(HERE)
MODE = sys.argv[1] if len(sys.argv) > 1 else "one"
IDLE_S = int(sys.argv[2]) if len(sys.argv) > 2 else 30
ADMIN = os.environ.get("BENCH_ADMIN_URL", "postgresql://postgres@127.0.0.1:5433/postgres")
DB = ADMIN.rsplit("/", 1)[0] + "/mumu_ramtest"
API = os.environ.get("FAKE_TG_URL", "http://127.0.0.1:8099")
RUST_BIN = os.environ.get("RUST_BIN") or os.path.join(REPO, "rust", "mumu-core-poc", "target", "release",
                                                     "mumu-core-poc")
TOKENS = {p: f"{1000 + i}:AAH{p}fakefakefakefakefakefakefakefak"
          for i, p in enumerate(["SBOT", "CBOT", "DBOT", "ABOT", "MBOT"])}


def rss_tree(pid: int) -> "dict[int, tuple[str, float]]":
    """{pid: (command, resident MB)} for pid and every process below it."""
    out, todo = {}, [pid]
    while todo:
        p = todo.pop()
        try:
            with open(f"/proc/{p}/status") as f:
                rss = next(int(l.split()[1]) for l in f if l.startswith("VmRSS:")) / 1024
            with open(f"/proc/{p}/cmdline", "rb") as f:
                cmd = f.read().replace(b"\0", b" ").decode(errors="replace")[:80]
            out[p] = (cmd, rss)
            with open(f"/proc/{p}/task/{p}/children") as f:
                todo += [int(c) for c in f.read().split()]
        except (FileNotFoundError, StopIteration, ProcessLookupError):
            pass
    return out


def api(path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


def command(user: int, text: str) -> dict:
    return {"message": {"message_id": int(time.time() * 1000) % 10**9, "date": int(time.time()),
                        "chat": {"id": user, "type": "private", "first_name": f"U{user}"},
                        "from": {"id": user, "is_bot": False, "first_name": f"U{user}", "language_code": "en"},
                        "text": text, "entities": [{"type": "bot_command", "offset": 0, "length": len(text)}]}}


def wait_done(limit: int = 1800) -> "float | None":
    """Seconds until everything sent was fetched and the replies stopped for 15 s."""
    start, last, quiet = time.time(), -1, 0
    while time.time() - start < limit:
        pending = api("/pending")["pending"]
        sent = api("/calls").get("sendMessage", 0)
        quiet = quiet + 5 if (sent == last and pending == 0) else 0
        last = sent
        if quiet >= 15:
            return time.time() - start
        time.sleep(5)
    return None


def main() -> None:
    with psycopg.connect(ADMIN, autocommit=True) as conn:
        conn.execute("DROP DATABASE IF EXISTS mumu_ramtest WITH (FORCE)")
        conn.execute("CREATE DATABASE mumu_ramtest")
    key = os.path.join(HERE, ".data_key")
    with open(key, "w") as f:
        f.write("dGVzdC1rZXktdGVzdC1rZXktdGVzdC1rZXktMDEyMzQ=\n")   # a test key, never a real one
    env = dict(os.environ, DATABASE_URL=DB, DATA_KEY_FILE=key, ADMIN_ID="42", LOG_TO_FILES="0",
               FAKE_TG_URL=API, TELEGRAM_API_URL=API, PYTHONPATH=os.path.join(HERE, "site"),
               MUMU_PROCESSES=MODE if MODE != "rust" else "one", MALLOC_ARENA_MAX="2")
    for prefix, token in TOKENS.items():
        env[f"{prefix}_TOKEN"] = token
        env[f"{prefix}_USERNAME"] = f"fake{token.split(':')[0]}_bot"
    cmd = [RUST_BIN] if MODE == "rust" else [sys.executable, "main.py"]
    log = open(os.path.join(HERE, f"run-{MODE}.log"), "w")
    proc = subprocess.Popen(cmd, cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT)

    def sample(label: str) -> None:
        tree = rss_tree(proc.pid)
        print(f"{label:>14}: {sum(v[1] for v in tree.values()):7.1f} MB in {len(tree)} process(es)", flush=True)

    try:
        time.sleep(15)
        for t in range(0, IDLE_S, 10):
            sample(f"idle {15 + t}s")
            time.sleep(10)
        people = range(5_000_001, 5_000_001 + int(os.environ.get("PEOPLE", "200")))
        repeat = int(os.environ.get("REPEAT", "1"))
        for token in TOKENS.values():
            api("/inject", {"token": token, "updates": [command(u, c) for _ in range(repeat)
                                                         for c in ("/start", "/help") for u in people]})
        for i in range(3):
            time.sleep(10)
            sample(f"crowd +{(i + 1) * 10}s")
        took = wait_done()
        print(f"every reply sent {took and round(took)} s later", flush=True)
        sample("done")
        time.sleep(30)
        sample("done +30s")
        if os.environ.get("TYPES_ON_SIGNAL") and MODE != "rust":
            os.kill(proc.pid, signal.SIGUSR1)
            time.sleep(15)
        for pid, (cmdline, mb) in sorted(rss_tree(proc.pid).items()):
            print(f"   {mb:6.1f} MB  {cmdline}")
        print("fake API calls:", api("/calls"))
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
        os.remove(key)


if __name__ == "__main__":
    main()
