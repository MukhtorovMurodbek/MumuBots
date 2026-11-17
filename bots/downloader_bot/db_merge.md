# `db_merge.py`

[↑ downloader_bot](README.md) · [code](db_merge.py)

Reconcile DownloaderBot's two databases (e.g. a laptop's local DB and the cloud DB) into one, without ever deleting or overwriting a row that already
exists.

```text
python db_merge.py --from local --into cloud --dry-run   # preview first
python db_merge.py --from local --into cloud             # actually do it
```

"local" and "cloud" are aliases read from LOCAL_DATABASE_URL and
CLOUD_DATABASE_URL (in the environment, or a .env file next to this
script) -- "local" falls back to this bot's own DATABASE_URL from db.py if
LOCAL_DATABASE_URL isn't set. A full postgresql:// URL can also be passed directly instead of an alias.

```text
POLICY -- read this before running it for real
  - Additive only. A row that already exists in the target (matched by
    that table's natural key -- NEVER the internal numeric id, which is
    meaningless across two independently-run databases) is left completely
    untouched. Nothing is ever deleted, and no existing row's values are
    ever changed by this script.
  - Because of that, this can never destroy data. The worst case is that a
    handful of rows needed a human judgement call and got skipped and
    printed instead of guessed at automatically -- both values are printed for a decision by hand; the script never guesses.
  - Always run with --dry-run first. It runs the exact same logic inside a
    transaction that gets rolled back instead of committed, and prints what
    it *would* do.
```

_At `def load_dotenv_if_present() -> None:`:_

activity_events (per-update telemetry backing /status's active-user counts)
is deliberately NOT merged here -- it's high-volume, has no real natural
key (occurred_at is a timestamp, not an identity), and has zero value
once it's more than an hour or two old. Losing it on a redeploy is fine.
