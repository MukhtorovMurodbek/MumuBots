# `db_merge.py`

[↑ anon_bot](README.md) · [code](db_merge.py)

Reconcile AnonBot's two databases (e.g. a laptop's local DB and the cloud DB) into one, without ever deleting or overwriting a row that already
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
  - anon_conversations is the one table with an internal foreign key
    (anon_follower_state / anon_relay point at anon_conversations.id). Its
    id is always freshly assigned by the target database; this script
    tracks the old-id -> new-id mapping in memory for the duration of the
    run and rewrites the matching anon_follower_state / anon_relay rows to
    point at the right place. If both databases coincidentally used the
    same conv_number for a genuinely different conversation between the
    same two people (recognised by a different started_at), the incoming
    one is renumbered instead of merged into the existing row.
  - Known limitation: if a conversation was still in the middle of a
    reply-chain at the exact moment hosts were switched, replies to messages
    sent before the switch won't resolve on the new side until the next merge -- the bot always starts fresh routing state after a switch, same
    as it would after a wiped DB. Everything else merges cleanly.
  - Always run with --dry-run first. It runs the exact same logic inside a
    transaction that gets rolled back instead of committed, and prints what
    it *would* do.
  - Personal data is sealed (2.1.1): every Telegram id in these tables is a
    pseudonym, and the ids the bot must read back sit beside it in sealed
    columns. Both databases must have been sealed with the SAME data key --
    family.settings 'data_key:v1' must exist in both, be equal, and be the key
    this machine's DATA_KEY_FILE holds -- or nothing is merged. A sealed value
    is compared by what it opens to (the same person sealed twice gives two
    different ciphertexts), and a source row whose sealed value does not open,
    or does not belong to its row, is skipped and counted, never copied.
    This script never prints an id, a username or a sealed value.
```

_At `PLAIN_TABLES = [`:_

"ids" are the columns that hold a person or chat as a pseudonym. "sealed" maps
a sealed column to (purpose, "id" or "text", the pseudonym column it must
match). Two databases sealed with one key hold the same pseudonym for the
same person, so the keys compare as stored; the sealed bytes do not (a fresh
nonce each time), so rows are compared by what the seals open to.

### `MergeRefused`

The two databases cannot be merged as they stand. Nothing was written.

_At `def load_dotenv_if_present() -> None:`:_

activity_events (per-update telemetry backing /status's active-user counts)
is deliberately NOT merged here -- it's high-volume, has no real natural
key (occurred_at is a timestamp, not an identity), and has zero value
once it's more than an hour or two old. Losing it on a redeploy is fine.

### `key_stamp()`

The fingerprint of the data key this database was sealed with, or None
when it has never been stamped (or has no family.settings at all).

### `require_same_key()`

Raises MergeRefused unless both databases carry the same data-key stamp
AND it is the key this machine holds -- a pseudonym or a seal made under
another key means nothing here, and merging them would plant rows no bot
could ever read.

### `_opened()`

`row` with every sealed value replaced by what it opens to, or None
when one does not open, or does not belong to the pseudonym beside it.

### `_described()`

A row for the report: its pseudonyms and plain columns, with each sealed
column elided -- the report never carries a ciphertext or what it hides.

### `merge_anon_conversations()`

Returns {source anon_conversations.id -> target id} for every source
conversation. A conversation's \*identity\* is (inbox holder, follower, started_at) -- conv_number is only a display counter and can coincide
between two independently-run databases, so it's not part of the
identity match.

- At `try:`:
  Both people are pseudonyms, and each sealed id must open to the person
  its pseudonym stands for. A row that fails is left behind, and what
  pointed at it is reported as orphaned below.

### `run_merge()`

- At `with closing(db.connect(src_dsn)) as src_conn, closing(db.connect(dst_dsn)) as dst_conn:`:
  Before anything is written, even by init_db: two databases that were not
  sealed with one key have nothing in common to merge on.
- At `db.init_db(dst_dsn)`:
  The family shares one database with a schema per bot, so the target
  side may be a brand-new empty schema (a freshly deployed Railway
  database, say). Idempotent, and skipped on --dry-run so a preview
  still cannot write anything at all.
