# `family_db.py`

[↑ manager_bot](README.md) · [code](family_db.py)

_At `SYSTEM_SCHEMAS = (`:_

Schemas that belong to Postgres itself, or to the Supabase platform,
rather than to the family. The Supabase-managed ones are recreated per
project, are never the family's own data, and several of them carry
things this CSV-merge tool cannot round-trip -- generated columns
(auth.identities.email) and views exposed as tables (vault.secrets).
list_tables() also always skips pg\_% and supabase\_%.

### `list_tables()`

- At `"WHERE table_type = 'BASE TABLE' AND table_schema NOT LIKE 'pg\\_%%' "`:
  %% rather than %: this statement carries parameters, so psycopg reads
  a bare % as the start of a placeholder.

### `columns_of()`

- At `"AND is_generated = 'NEVER' "`:
  A generated column cannot appear in a COPY column list (either
  direction), and a STORED one is recomputed on insert anyway, so
  dropping it from both the backup and the restore is necessary and
  lossless. Supabase's auth.identities.email is the one that bites.

## backup

### `backup()`

`leave_out`: table names (in any schema) not to copy -- the volume's
reserve copy skips the logs and counters (reserve_copies.LEAVE_OUT).
`say` gets the per-table lines; the bot's frequent copies pass a no-op.

- At `with closing(psycopg.connect(dsn)) as conn, zipfile.ZipFile(`:
  Level 9: CSV is most of what a database is, it compresses several
  times over, and the archive is made rarely and kept a long time --
  including as a Telegram document, where every megabyte under the 50 MB
  ceiling is a part fewer to join back together.
- `rows = max(payload.count(b"\n") - 1, 0)` — minus the header

## restore

### `restore()`

- At `restored.append({**entry, "columns": usable})`:
  Resync from the columns that actually landed, not the ones
  the archive happened to carry. A column the target does not
  have is not merely empty to pg_get_serial_sequence -- it
  raises UndefinedColumn, which would abort the whole restore
  transaction \*after\* every row had been copied into it.

### `_copy_into()`

COPY straight in. `usable` may be narrower than the archive's columns,
in which case the CSV is re-read through a staging table instead.

### `_merge_into()`

Additive: load into a staging copy of the table, then insert only the
rows the target does not already have. ON CONFLICT DO NOTHING with no
conflict target covers every unique constraint the table has at once,
which is exactly the "never overwrite, never delete" policy the bots'
own db_merge.py already follows.

### `_project_csv()`

Drops columns the target no longer has. Rare -- it only happens when
restoring an archive taken before a schema change -- so this goes
through Python's csv module rather than complicating the COPY path.

### `_resync_sequences()`

Every bot's tables use BIGSERIAL ids. Copying rows carries their id
values across but not the sequence behind them, so without this the very
next insert would collide with a restored row.
