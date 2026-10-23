"""Is any Telegram id left in the clear in AnonBot's tables?

Since 2.1.1 every column that names a person or a chat holds
`vault.pseudonym(id)`, a BIGINT below -2**62, and no real Telegram id (people
are positive, chats no lower than about -10**13) falls in that range. So "no
real id is stored" is a question the database can answer by itself, without
knowing which ids to look for: any such column holding a value at or above
the ceiling is an id in the clear.

The columns are found, not listed: every BIGINT column called `user_id`,
`chat_id` or `*_user_id` in a schema that has an `anon_*` table. A column
added later is covered on the day it is added.

    python tests/sealed_check.py      # every such schema in TEST_DATABASE_URL

Exit status 1 and a list of "schema.table.column: n" when anything is left.
"""
from __future__ import annotations

import os
import sys

CEILING = -(2 ** 62)          # vault.PSEUDONYM_CEILING; kept here so this file stands alone

_IDENTITY_COLUMNS = (
    "SELECT table_name, column_name FROM information_schema.columns "
    "WHERE table_schema = %s AND data_type = 'bigint' "
    "AND (column_name IN ('user_id', 'chat_id') OR column_name LIKE '%%\\_user\\_id') "
    "ORDER BY table_name, column_name"
)


def schemas_with_anon_tables(conn) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT table_schema FROM information_schema.tables "
        "WHERE table_name LIKE 'anon\\_%%' ORDER BY 1").fetchall()]


def real_ids_left(conn, schema: str) -> tuple[list[str], int, int]:
    """(findings, identity columns looked at, rows looked at) for one schema.
    A finding is 'table.column: n value(s) are not pseudonyms'; the values
    themselves are never read out."""
    findings, columns, rows = [], 0, 0
    for table, column in conn.execute(_IDENTITY_COLUMNS, (schema,)).fetchall():
        columns += 1
        total, plain = conn.execute(
            f'SELECT count("{column}"), count(*) FILTER (WHERE "{column}" >= %s) '
            f'FROM "{schema}"."{table}"', (CEILING,)).fetchone()
        rows += total
        if plain:
            findings.append(f"{table}.{column}: {plain} value(s) are not pseudonyms")
    return findings, columns, rows


def main() -> int:
    import psycopg

    dsn = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/botfamily_test")
    failed = False
    with psycopg.connect(dsn) as conn:
        schemas = schemas_with_anon_tables(conn)
        for schema in schemas:
            findings, columns, rows = real_ids_left(conn, schema)
            status = "FAIL" if findings else "ok  "
            print(f"  {status} {schema}: {columns} identity column(s), {rows} value(s) looked at")
            for finding in findings:
                print(f"       {schema}.{finding}")
            failed = failed or bool(findings)
        if not schemas:
            print("  (no schema with anon_* tables)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
