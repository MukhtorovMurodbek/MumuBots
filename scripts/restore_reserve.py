#!/usr/bin/env python3
"""Restore the family's database from its reserve copies, for an emergency only.

    python scripts/restore_reserve.py --database-url-env TARGET_URL --key-file /path/to/mumu_data_key \\
        --copy botfamily_2026-11-03_1300.zip \\
        [--latest latest.mumu-reserve] [--erasures erasures.jsonl]

docs/reserve-copies.md is the manual; bots/shared/reserve.py says why the
erasure notes exist. In order, it:

  1. refuses a copy older than reserve.KEEP_DAYS (the promise in LEGAL.md),
     and a database whose bots look alive (a heartbeat in the last 3 minutes);
  2. restores --copy, the whole database: the nightly copy from the owner's
     Telegram (a plain .zip) or from the volume (nightly.mumu-reserve, sealed),
     into the tables the bots made when they were started once against this
     database ("replace": each table is emptied, then loaded);
  3. restores --latest over it, if it is newer: the volume's current copy of
     everything but the logs and counters;
  4. erases again everyone whose erasure note -- from the restored `erasures`
     table and from --erasures, the volume's journal -- is newer than --copy,
     with each bot's own /deletemydata code, and keeps the journal's notes in
     the table.

It prints table names, row counts and how many people were erased again;
nothing personal, and never the database address (read from the environment
variable you name). The key is the data key, as for encrypt_personal_data.py.

A known limit: someone who erased and then came back to the same bot before
the emergency loses what they did after coming back, if --copy is older than
their erasure. Restoring the newest copies keeps that window under a day.

Exit status: 0 done, 2 refused (nothing changed), 1 failed.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import importlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
MANAGER = "manager_bot"


def _encrypt_script():
    """encrypt_personal_data.py's loader and key handling: one way of giving the
    bots' modules a key and a database outside a running family."""
    spec = importlib.util.spec_from_file_location("encrypt_personal_data", ROOT / "scripts" / "encrypt_personal_data.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Refusal(Exception):
    pass


def read_copy(path: Path, copies) -> "tuple[bytes, datetime, str]":
    """(archive, when it was taken, its kind): a sealed reserve file or a plain family_db zip."""
    blob = path.read_bytes()
    header = copies.read_header(blob)
    if header is not None:
        return copies.open_archive(blob), datetime.fromisoformat(header["taken_at"]), header["kind"]
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        manifest = json.loads(archive.read("MANIFEST.json"))
    return blob, datetime.fromisoformat(manifest["created_at"]), "plain"


def journal_notes(path: "Path | None") -> list:
    """The volume journal's notes; a torn last line (a write cut short) is skipped."""
    if path is None:
        return []
    notes = []
    for line in path.read_text(encoding="ascii").splitlines():
        try:
            entry = json.loads(line)
            notes.append((entry["bot"], entry["db_schema"], datetime.fromisoformat(entry["at"]),
                          base64.b64decode(entry["user"])))
        except (ValueError, KeyError, TypeError):
            print("  (a damaged line in the journal was skipped)")
    return notes


def table_notes(url: str, family_schema: str) -> list:
    with psycopg.connect(url) as conn:
        if conn.execute("SELECT to_regclass(%s)", (f"{family_schema}.erasures",)).fetchone()[0] is None:
            return []
        return [(bot, schema, at, bytes(sealed)) for bot, schema, at, sealed in conn.execute(
            f"SELECT bot, db_schema, erased_at, sealed_user FROM {family_schema}.erasures").fetchall()]


def erase_again(notes: list, since: datetime, script, reserve, say=print) -> "tuple[int, int, int]":
    """Each bot's own erase for every note newer than `since`: (people, rows, failed).
    One that fails is counted and the rest go on: the data is restored by now, and
    every other erasure still has to happen."""
    people, rows, failed = set(), 0, 0
    for bot, schema, at, sealed in sorted(notes, key=lambda note: note[2]):
        if at <= since:
            continue
        try:
            vault = importlib.import_module(f"{bot}.vault")
            user_id = vault.open_id(sealed, reserve.ERASURE_PURPOSE)
            if (bot, user_id) in people:
                continue
            db = script.load_bot_db(bot)
            if db.DB_SCHEMA != schema:
                raise RuntimeError(f"the note is for schema {schema}; this run's {bot} uses {db.DB_SCHEMA}")
            rows += db.erase_user(user_id)
            rows += importlib.import_module(f"{bot}.lifecycle").forget_user(user_id)
            rows += importlib.import_module(f"{bot}.family_link").forget_problem_reporter(user_id)
            people.add((bot, user_id))
        except Exception as exc:
            failed += 1
            say(f"  ! a note for {bot} of {at:%Y-%m-%d %H:%M} UTC could not be applied: {type(exc).__name__}: {exc}")
    return len(people), rows, failed


def keep_journal_notes(url: str, family_schema: str, notes: list, present: list) -> int:
    """The journal's notes the restored table lacks, so the next restore has them too."""
    have = {(bot, schema, at) for bot, schema, at, _ in present}
    missing = [note for note in notes if note[:3] not in have]
    with psycopg.connect(url) as conn:
        for bot, schema, at, sealed in missing:
            conn.execute(f"INSERT INTO {family_schema}.erasures (bot, db_schema, erased_at, sealed_user) "
                         f"VALUES (%s, %s, %s, %s)", (bot, schema, at, sealed))
        conn.commit()
    return len(missing)


def restore(url: str, copy_path: Path, latest_path: "Path | None", journal: "Path | None",
            script, now: "datetime | None" = None, say=print) -> None:
    now = now or datetime.now(timezone.utc)
    copies = importlib.import_module(f"{MANAGER}.reserve_copies")
    family_db = importlib.import_module(f"{MANAGER}.family_db")
    reserve = importlib.import_module(f"{MANAGER}.reserve")
    link = script.load_family_link()

    archive, taken_at, kind = read_copy(copy_path, copies)
    if now - taken_at > timedelta(days=reserve.KEEP_DAYS):
        raise Refusal(f"that copy was taken {taken_at:%Y-%m-%d}, more than {reserve.KEEP_DAYS} days ago. "
                      f"No copy that old is ever restored (LEGAL.md).")
    latest = None
    if latest_path is not None:
        latest_archive, latest_at, latest_kind = read_copy(latest_path, copies)
        if latest_kind != "latest":
            raise Refusal("--latest takes the volume's latest.mumu-reserve")
        if latest_at > taken_at:
            latest = latest_archive
        else:
            say(f"--latest ({latest_at:%Y-%m-%d %H:%M} UTC) is not newer than --copy; it is not used.")
    with psycopg.connect(url) as conn:
        try:
            script.refuse_if_alive(conn, link, script.default_families(conn), script.ALIVE_WITHIN_SECONDS,
                                   "nothing was restored")
        except script.Refusal as exc:
            raise Refusal(str(exc)) from None

    with tempfile.TemporaryDirectory(prefix="mumu-restore-") as folder:
        say(f"Restoring the {kind} copy of {taken_at:%Y-%m-%d %H:%M} UTC:")
        first = Path(folder) / "copy.zip"
        first.write_bytes(archive)
        family_db.restore(url, first, "replace", None, False)
        if latest is not None:
            say(f"\nOver it, the volume's current copy of {latest_at:%Y-%m-%d %H:%M} UTC:")
            second = Path(folder) / "latest.zip"
            second.write_bytes(latest)
            family_db.restore(url, second, "replace", None, False)

    present = table_notes(url, link.FAMILY_SCHEMA)
    from_journal = journal_notes(journal)
    people, rows, failed = erase_again(present + from_journal, taken_at, script, reserve, say)
    added = keep_journal_notes(url, link.FAMILY_SCHEMA, from_journal, present)
    say(f"\nErased again: {people} person/bot pair(s) who used /deletemydata after the copy "
        f"({rows} row(s)). {added} note(s) from the journal kept in the table.")
    if failed:
        raise RuntimeError(f"{failed} erasure note(s) could not be applied (above). Don't start the "
                           f"bots until they are: fix the cause and run this again.")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if any("://" in arg for arg in argv):
        print("The database address is never taken as an argument. Put it in an environment "
              "variable and give --database-url-env that variable's NAME.", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--database-url-env", required=True, metavar="NAME")
    keys = parser.add_mutually_exclusive_group(required=True)
    keys.add_argument("--key-file", metavar="PATH")
    keys.add_argument("--key-env", metavar="NAME")
    parser.add_argument("--copy", required=True, type=Path,
                        help="the nightly copy: botfamily_<date>.zip from Telegram, or the volume's nightly.mumu-reserve")
    parser.add_argument("--latest", type=Path, help="the volume's latest.mumu-reserve")
    parser.add_argument("--erasures", type=Path, help="the volume's erasures.jsonl")
    parser.add_argument("--yes", action="store_true", help="don't ask before replacing the tables")
    args = parser.parse_args(argv)

    url = os.environ.get(args.database_url_env)
    if not url:
        print(f"The environment variable {args.database_url_env} is not set.", file=sys.stderr)
        return 1
    if not args.yes and input("Every table in the copy is EMPTIED in that database, then loaded. "
                              "Continue? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Cancelled -- nothing was touched.")
        return 2

    key_copy = None
    if args.key_env:
        text = os.environ.get(args.key_env, "").strip()
        if not text:
            print(f"The environment variable {args.key_env} is not set.", file=sys.stderr)
            return 1
        handle, key_copy = tempfile.mkstemp(prefix="mumu-data-key-")
        with os.fdopen(handle, "w", encoding="ascii", errors="replace") as out:
            out.write(text + "\n")
        args.key_file = key_copy
    # The bots' modules read both at import.
    os.environ["DATABASE_URL"] = url
    os.environ["DATA_KEY_FILE"] = args.key_file
    try:
        script = _encrypt_script()
        script.hand_over_key(args.key_file, {})
        restore(url, args.copy, args.latest, args.erasures, script)
        return 0
    except Refusal as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if key_copy is not None:
            with contextlib.suppress(OSError):
                os.remove(key_copy)


if __name__ == "__main__":
    sys.exit(main())
