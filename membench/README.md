# membench: how much memory the bots use, measured

An experiment branch, not for `main`. `docs/memory-and-rust.md` (on `2.2.0`) has the numbers and the
decision they led to.

- `fake_telegram.py`: a fake Bot API (getMe, getUpdates, sendMessage, ... and `/inject` to queue
  updates for a bot, `/pending`, `/calls`). Enough for the five bots to start, poll and answer.
- `measure.py`: starts `main.py` (or the Rust runtime) against it, sends a crowd, prints the
  resident memory while idle, under the crowd, and once every reply is out.
- `site/sitecustomize.py`: points python-telegram-bot at the fake API in every process it starts.
- `smaps.py <pid>`: where a process's memory is (heap, Python arenas, thread stacks, files).

```bash
python membench/fake_telegram.py 8099 &
PEOPLE=2000 python membench/measure.py one
```

Needs a Postgres the script may create and drop `mumu_ramtest` on (`BENCH_ADMIN_URL`, default the
scratch cluster on port 5433). Restart `fake_telegram.py` between runs: it keeps what it was sent.
