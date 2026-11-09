# mumu-core-poc

The floor of a Rust runtime for MumuBots, for `docs/memory-and-rust.md`: five bots long-polling the
Bot API (`TELEGRAM_API_URL`), answering `/start` and `/help` in en/uz/ru, and writing who was active
to Postgres (`DATABASE_URL`) in batches, as the Python bots' `record_activity_batch` does. Not a
port of any bot.

```bash
cargo build --release
```

Measured with `membench/` (2026-11-09): 5 MB idle, 5.4 MB with 2,000 people at once.
