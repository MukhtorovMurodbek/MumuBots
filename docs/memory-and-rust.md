# Memory, and whether to rewrite in Rust

The owner, 2026-11-09: *"Consider writing the code in Rust for RAM efficiency, either rewriting existing code or the new ones."*

**Decision: not now.** Python stays. The part of the memory that grows with users is fixed in Python instead (`UpdateQueue`, below). The numbers and the reasons follow, and so does when Rust *would* be worth it.

## Measured (2026-11-09)

The real `main.py`, against a fake Telegram Bot API on the same machine, Postgres 17. "People at once" means every person sends `/start` and `/help` to all five bots within seconds.

| | Python 2.2.0, five bots in one process | Rust runtime, same workload |
|---|---|---|
| Idle | 94 MB | 5 MB |
| 200 people at once (2,000 commands) | 134 MB | 5 MB |
| 2,000 people at once (20,000 commands) | **399 MB** before this change, **170 MB** with it | 5.4 MB |

- A process per bot (`MUMU_PROCESSES=separate`) costs ~85 MB each, plus 50 MB for the supervisor: about 480 MB for five. Keep the public bots in one process.
- Production runs two services today: MumuCombined (four bots, ~95–135 MB) and MumuManager (ManagerBot, ~80 MB). That's ~0.2 GB, about $2 a month at Railway's ~$10 per GB-month, inside the $5 the Hobby plan includes.
- **The Rust column is a floor, not a port.** It is five bots polling, answering two commands in three languages, and writing activity to Postgres in batches. A full port adds the features' own data and logic: expect tens of MB, not hundreds. Its speed isn't comparable either, because the Python bots do far more per message.

## What was really wrong, and the fix

The crowd case was the problem, not Python's base cost. python-telegram-bot makes a task of every update the moment it is fetched, so 20,000 queued messages became 20,000 live tasks. Afterwards Python's allocator kept the memory: 41 MB of live objects spread over 265 arenas (265 MB), and an arena is only returned when it's completely empty.

`UpdateQueue` (`bots/shared/shared_features.py`) holds polling back while a bot already has `UPDATES_IN_MEMORY_MAX` updates (default 256) queued, waiting their person's turn, or being handled. Telegram keeps the rest for 24 hours and hands them over in order. The same crowd now peaks at 170 MB and is answered a little sooner. 200 people at once cost the same as before.

## Why not a rewrite now

- **Money:** it saves about $2 a month of usage that is already included in the plan.
- **Size:** 54,000 lines of Python, every text in three languages, and conversions that depend on Python-only libraries (PyMuPDF, python-docx, openpyxl, mammoth, rawpy). yt-dlp has no Rust equivalent and would stay a Python subprocess anyway.
- **Risk:** the owner's first rule is that nothing in an earlier version may be lost. A big rewrite is the surest way to lose something.

## When Rust becomes worth it, and how

Worth it when one of these is true:
- moving to a small server (512 MB–1 GB of RAM), where 100+ MB of Python matters;
- RAM becomes most of a bill that has outgrown the included $5;
- a new always-on component is being written from scratch anyway.

How: one piece at a time, behind the same database, never all at once.
1. The always-on core first: polling, routing, texts, the database. Python stays as worker processes for conversions and yt-dlp.
2. One bot at a time, run side by side with the Python one (`BOTS=` decides who polls), until it does everything the Python one does.

The runtime measured above, and the fake Bot API used to measure both, are on the branch `experiment/rust-runtime`.

## Measuring again

`experiment/rust-runtime` has `membench/`: a fake Bot API and a script that starts `main.py` against it, sends a crowd, and prints the memory over time. Run it after any change that touches how updates are handled.
