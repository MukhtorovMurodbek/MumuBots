"""Make the scratch database look the way scripts/encrypt_personal_data.py leaves a real one.

2.1.2CHL's bots refuse to start (family_link.ensure_data_key) on a database that
is not stamped with the key in use and with `sealed:anon:v1`. A scratch database
shared by many suites, with rows left behind by the last run, is exactly such a
database -- so every suite that runs a bot's main() against it stamps it first,
as the script would have. The stamp is the key's fingerprint, so a suite that
runs under another key file just replaces it.

The guard itself is tested in seal_scenarios.py, on a family of its own.
"""
from __future__ import annotations


def stamp_database(dsn: str, family: str = "family") -> None:
    import psycopg
    import vault

    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{family}"')
        conn.execute(f'CREATE TABLE IF NOT EXISTS "{family}".settings (key TEXT PRIMARY KEY, value TEXT)')
        for key, value in (("data_key:v1", vault.fingerprint()), ("sealed:anon:v1", "1")):
            conn.execute(
                f'INSERT INTO "{family}".settings (key, value) VALUES (%s, %s) '
                f'ON CONFLICT (key) DO UPDATE SET value = excluded.value', (key, value))
