#!/usr/bin/env python3
"""Seal the personal data a database written by 2.1.1 (or earlier) holds in the
clear, and stamp the database with the key that did it.

    python scripts/encrypt_personal_data.py --database-url-env DATABASE_URL \\
        --key-file /path/to/mumu_data_key --dry-run         # first: changes nothing
    python scripts/encrypt_personal_data.py --database-url-env DATABASE_URL \\
        --key-file /path/to/mumu_data_key                   # then for real

Stop all five bots first, and keep a copy of the database (ManagerBot's
/backup, or db_backup.ps1): plain-text columns are dropped, and a backup taken
afterwards cannot give them back. Keep a copy of the key file offline too --
losing it loses every sealed value and every way to find a person by id
(bots/shared/vault.py says why).

THE STAGES. The owner seals the bots one group of tables at a time, a release
each, so this script converts by stage (STAGES below). 2.2.0 has two:

  anon   AnonBot's tables, in every schema that has `anon_conversations`
         (anon_bot, testbot_anon_bot, ...): identities become pseudonyms, the
         readable owner/follower ids go into sealed columns, usernames are
         sealed, the plain columns are dropped. And, for the whole family, the
         saved conversation state (`runtime_state`, every bot's schema) and
         the update waitlist: rows that are not sealed are deleted, because
         they live hours.
  names  usernames and names in Sticker, Convert and DownloaderBot: the payment
         records' usernames (star_transactions) and the sticker packs' owner
         (packs) go into sealed columns, and the plain columns are dropped.

A later release adds its stage ("convert", "downloader", "sticker",
"family-reports") to STAGES and to the bots' own SEAL_STAGES
(bots/shared/family_link.py), and runs this script again: a stage already done is
converted again at no change, and is stamped again at no change.

WHAT IT DOES, in ONE transaction:
  1. refuses -- before touching anything -- a database stamped with another
     key, and a database whose bots look alive (a heartbeat in the last
     3 minutes; it names the bot ids and nothing else);
  2. for each stage, its bot's conversion on each of its schemas;
  3. the family's own conversion (saved state, update waitlist);
  4. every verification: nothing readable is left, and everything sealed
     opens under its purpose and belongs to its row;
  5. stamps the family: `data_key:v1` = the key's fingerprint, and
     `sealed:<stage>:v1` = 1 for each stage -- what the bots check at start.
     TestBot's family (`testbot_family`) is stamped too when TestBot's
     schemas are here; a database that has them but no `testbot_family`
     gets its settings table made, and the output says so;
  6. COMMIT -- or ROLLBACK, on any error and always under --dry-run;
  7. after a COMMIT, VACUUM (FULL, ANALYZE) of every table it converted, and
     of every saved-state and waitlist table: an UPDATE, a DELETE and a dropped
     column leave the old, readable rows on disk until the table is rewritten,
     and the column statistics keep samples of the old values until they are
     taken again. A table whose file did not change (Postgres skips, with only
     a warning, a table the role does not own) counts as failed. It locks each
     table while it does, which the stopped bots do not mind.

Running it again changes nothing. It prints the number of rows per table and
nothing personal; the database address is read from the environment variable
you name and never printed. It gives the key to every bot's copy of the vault
(each bot has its own) and refuses unless they all load the same key; then it
prints the key file's fingerprint (16 hex digits that name the key without
revealing it) and the fingerprint the database is stamped with, if it is.

    --vacuum-only   convert nothing; run step 7 alone, for a run that
                    committed but could not vacuum. Refused unless the
                    database is already stamped with this key, and while
                    any bot looks alive.

Exit status: 0 done (or a dry run that worked), 2 refused, 1 failed, 3
converted and committed but a VACUUM failed (rerun with --vacuum-only).
"""
from __future__ import annotations

import argparse
import contextlib
import importlib
import importlib.util
import os
import re
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The bots' modules are not importable by path: each bot has its own copy. main.py's own loader
# finds them: `anon_bot.db`, and `anon_bot.vault` / `anon_bot.family_link` beside it, as AnonBot sees them.
_BOTS_REGISTERED = False
# The bots that have the `vault` module: each imports a copy of its own.
_VAULT_FOLDERS: "list[str]" = []


def _bot_modules_ready() -> None:
    """Register the five bots' packages with main.py's loader, once."""
    global _BOTS_REGISTERED
    if _BOTS_REGISTERED:
        return
    main = sys.modules.get("main")
    if main is None or getattr(main, "ROOT", None) != ROOT:
        spec = importlib.util.spec_from_file_location("main", ROOT / "main.py")
        main = importlib.util.module_from_spec(spec)
        sys.modules["main"] = main          # @dataclass looks its module up here while main.py runs
        spec.loader.exec_module(main)
    for bot in main.discover()[0]:
        main.register(bot)
        if "vault" in bot.shared or "vault" in bot.files:
            _VAULT_FOLDERS.append(bot.folder)
    sys.meta_path.insert(0, main._BotFinder)
    _BOTS_REGISTERED = True


def bot_vaults() -> list:
    """Every bot's copy of the `vault` section, as main.py's loader gives them."""
    _bot_modules_ready()
    return [importlib.import_module(f"{folder}.vault") for folder in _VAULT_FOLDERS]


class KeyProblem(Exception):
    """The key file cannot be used, in the vault's own words."""


def hand_over_key(key_file: str, modules: dict) -> "dict[str, str]":
    """Point every vault there is at `key_file` and load the key in each: every
    bot's copy, and the copy each of `modules` uses (a later stage seals with
    its own bot's). Returns {the vault module's name: its key's fingerprint}.

    Until 2.2.0 only the first stage's bot was given the key: any other bot's
    copy kept the path it read from DATA_KEY_FILE at import, so a later stage
    would have sealed with another key (or none) than the one stamped."""
    vaults = {module.__name__: module for module in bot_vaults()}
    for module in modules.values():
        used = getattr(module, "vault", None)
        if isinstance(used, types.ModuleType) and hasattr(used, "require"):
            vaults.setdefault(used.__name__, used)
    found = {}
    for name, module in vaults.items():
        module.KEY_FILE = key_file
        module._keys = None
        try:
            found[name] = module.require()
        except module.VaultError as exc:
            raise KeyProblem(str(exc)) from None
    return found


REAL_FAMILY = "family"
TEST_FAMILY = "testbot_family"

STAGES = ["anon", "names"]
STAGE_FOLDER = {"anon": "anon_bot", "names": "family_link"}
STAGE_STEPS = {
    "anon": ("convert_personal_data_v1", "verify_personal_data_v1"),
    "names": ("convert_names_v1", "verify_names_v1"),
}

# How long after its last heartbeat a bot still counts as running (bots write
# one every 30 seconds).
ALIVE_WITHIN_SECONDS = 180


class Refusal(Exception):
    """A reason not to start, said in words and with nothing personal in it."""


def load_bot_db(folder: str):
    """`<folder>.db`, imported by package path through main.py's loader: the bots' db modules
    share a module name, so each is a module of its own bot's package."""
    _bot_modules_ready()
    return importlib.import_module(f"{folder}.db")


def load_family_link():
    """bots/shared/family_link.py, as the sealing bot sees it (its `db` and `lifecycle`
    are that bot's own; this script uses neither's pool, it opens its own connection)."""
    _bot_modules_ready()
    return importlib.import_module(f"{STAGE_FOLDER[STAGES[0]]}.family_link")


def default_families(conn) -> "list[str]":
    """The real family, always; TestBot's too when any of its schemas is here."""
    names = {row[0] for row in conn.execute(
        "SELECT DISTINCT schemaname FROM pg_catalog.pg_tables "
        "WHERE schemaname <> 'information_schema' AND schemaname NOT LIKE 'pg\\_%'").fetchall()}
    families = [REAL_FAMILY]
    if any(name.startswith("testbot_") for name in names):
        families.append(TEST_FAMILY)
    return families


def flatten(result) -> "list[tuple[str, object]]":
    """(label, count) pairs out of what a conversion returned: dicts of
    counts, and lists of notes about what was skipped. Numbers and table
    names only, so nothing a row held can come out here."""
    out: list = []

    def walk(path: list, value) -> None:
        if path and path[0] in ("changed", "checked"):
            path = path[1:]
        if isinstance(value, dict):
            for key, inner in value.items():
                walk(path + [str(key)], inner)
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                out.append((".".join(path) or "note", str(item)[:120]))
        elif isinstance(value, (int, bool)):
            out.append((".".join(path), value))

    walk([], result)
    return out


def tables_to_vacuum(conn, link, fams: "list[str]") -> "list[tuple[str, str]]":
    """(schema, table) of every table the conversion changes: each stage's
    tables in its bot's schemas, and the saved-state and waitlist tables in
    every schema of the families."""
    found: set = set()
    for family in fams:
        found |= set(link.personal_data_tables(conn, tuple(STAGES), family))
    return sorted(found)


def _filenode(conn, schema: str, table: str):
    """The table's file on disk (None for no such table). VACUUM FULL writes a new one."""
    row = conn.execute(
        "SELECT pg_relation_filenode(c.oid) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = %s AND c.relname = %s", (schema, table)).fetchone()
    return row[0] if row else None


def vacuum_tables(conn, tables: "list[tuple[str, str]]", say) -> int:
    """VACUUM (FULL, ANALYZE) each table on `conn`, which must be in autocommit
    mode (the statement cannot run in a transaction). FULL rewrites the table,
    so the old rows are no longer in its file; ANALYZE replaces its column
    statistics, whose samples (most common values, histogram bounds) were taken
    from the plain ids. A table counts as done only when its file has changed:
    Postgres skips a table the role does not own with a warning, not an error.
    A failure is said and counted and the rest are still done; returns how
    many failed. Names only."""
    from psycopg import sql

    try:
        conn.execute("SET lock_timeout = '60s'")
        conn.execute("SET statement_timeout = '3600s'")
    except Exception:                               # noqa: BLE001 -- the vacuums below say what matters
        pass
    failed = 0
    for schema, table in tables:
        try:
            before = _filenode(conn, schema, table)
            conn.execute(sql.SQL("VACUUM (FULL, ANALYZE) {}.{}").format(sql.Identifier(schema),
                                                                       sql.Identifier(table)))
            after = _filenode(conn, schema, table)
        except Exception as exc:                    # noqa: BLE001
            failed += 1
            say(f"  vacuum FAILED {schema}.{table} ({type(exc).__name__})")
            continue
        if before is None or after == before:
            failed += 1
            say(f"  vacuum FAILED {schema}.{table} (not rewritten: is this role the table's owner?)")
            continue
        say(f"  vacuumed {schema}.{table}")
    return failed


def refuse_if_alive(conn, link, fams: "list[str]", alive_within: int, nothing_done: str) -> None:
    """A database somebody may still be writing to (or, for a vacuum, whose
    every query a table lock would stall) is refused."""
    alive = [bot for family in fams for bot in link.fresh_heartbeats(conn, alive_within, family)]
    if alive:
        raise Refusal("these bots look alive (a heartbeat in the last "
                      f"{alive_within // 60} minutes): {', '.join(sorted(set(alive)))}. "
                      f"Stop them, wait, and run this again; {nothing_done}")


def require_stamps(conn, fingerprint: str, link, fams: "list[str]") -> None:
    """--vacuum-only converts nothing, so it must find a database that is
    already converted with this key: every family stamped with the key's
    fingerprint and with each stage."""
    for family in fams:
        stamped = link.read_stamp(conn, link.DATA_KEY_SETTING, family)
        if stamped is None:
            raise Refusal(f"{family} is not stamped: it has not been converted with this key, so there is "
                          "nothing to vacuum yet. Run the conversion without --vacuum-only")
        if stamped != fingerprint:
            raise Refusal(f"{family} is stamped with another data key than the one given; nothing was done")
        for stage in STAGES:
            if link.read_stamp(conn, link.sealed_setting(stage), family) != "1":
                raise Refusal(f"{family} is not stamped as converted for stage {stage}: "
                              "run the conversion without --vacuum-only")


def run(conn, fingerprint: str, modules: dict, say, families: "list[str] | None" = None,
        alive_within: int = ALIVE_WITHIN_SECONDS) -> "list[tuple[str, str]]":
    """The whole conversion on `conn`, which has a transaction open and is the
    caller's to commit or roll back. `modules` maps a bot's folder name to the
    module that has its convert/verify functions, and "family_link" to
    family_link; `say` prints a line. `families` is the family schemas to
    convert (default: the real one, and TestBot's if its schemas are here).
    Returns the (schema, table) pairs to vacuum once the caller has committed."""
    link = modules["family_link"]

    # A stage the bots demand and this script cannot convert would leave every
    # bot refusing to start after a "successful" run.
    missing = [stage for stage in link.SEAL_STAGES if stage not in STAGES]
    if missing:
        raise Refusal(f"the bots require stage(s) {', '.join(missing)} that this script does not convert")

    # Nothing may be left waiting on a lock a running bot holds, nor run for ever.
    conn.execute("SET LOCAL lock_timeout = '60s'")
    conn.execute("SET LOCAL statement_timeout = '600s'")

    fams = families or default_families(conn)

    # Refuse before changing anything: another key's database, and a database
    # somebody is still writing to.
    for family in fams:
        stamped = link.read_stamp(conn, link.DATA_KEY_SETTING, family)
        if stamped is not None and stamped != fingerprint:
            raise Refusal(f"{family} is stamped with another data key than the one given; "
                          f"nothing was changed")
    refuse_if_alive(conn, link, fams, alive_within, "nothing was changed")

    plan ={stage: [(family, schema) for family in fams
                    for schema in link.stage_schemas(conn, stage, family)] for stage in STAGES}
    for stage in STAGES:
        if not plan[stage]:
            say(f"note: stage {stage}: no schema to convert here.")

    for index, (step, name) in enumerate((("convert_personal_data_v1", "Converting"),
                                          ("verify_personal_data_v1", "Verifying"))):
        say(f"{name}:")
        for stage in STAGES:
            module = modules[STAGE_FOLDER[stage]]
            for _family, schema in plan[stage]:
                for label, count in flatten(getattr(module, STAGE_STEPS[stage][index])(conn, schema)):
                    say(f"  {stage} {schema} {label}: {count}")
        for family in fams:
            for label, count in flatten(getattr(link, step)(conn, family)):
                say(f"  {family} {label}: {count}")

    say("Stamping:")
    for family in fams:
        if link.ensure_settings_table(conn, family):
            say(f"  {family}: no settings table here; made one")
        link.write_stamps(conn, fingerprint, STAGES, family)
        if link.read_stamp(conn, link.DATA_KEY_SETTING, family) != fingerprint:
            raise Refusal("the data key stamp changed while this ran: another key was stamped meanwhile")
        for stage in STAGES:
            if link.read_stamp(conn, link.sealed_setting(stage), family) != "1":
                raise RuntimeError(f"{family} has a stage stamp for {stage} that is not 1; "
                                   f"it has to be looked at by hand")
        say(f"  {family}: stamped with this key, stages {', '.join(STAGES)}")
    return tables_to_vacuum(conn, link, fams)


_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def main(argv=None, modules: "dict | None" = None, connect=None,
         families: "list[str] | None" = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # argparse echoes what it cannot parse. An address on the command line
    # must not be echoed, so nothing that looks like one goes anywhere near it.
    if any("://" in arg or arg.lower().startswith(("--database-url=", "--url")) for arg in argv):
        print("The database address is never taken as an argument. Put it in an environment "
              "variable and give --database-url-env that variable's NAME.", file=sys.stderr)
        return 2

    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--database-url-env", required=True, metavar="NAME",
                        help="the name of the environment variable that holds the database URL "
                             "(the URL itself is never taken as an argument, and never printed)")
    key_group = parser.add_mutually_exclusive_group(required=True)
    key_group.add_argument("--key-file", metavar="PATH", help="the data key file")
    key_group.add_argument("--key-env", metavar="NAME",
                           help="the name of the environment variable that holds the data key, for a key "
                                "kept in a password manager rather than a file (the key itself is never "
                                "taken as an argument)")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--dry-run", action="store_true",
                            help="do everything, print the counts, and roll back")
    mode_group.add_argument("--vacuum-only", action="store_true",
                            help="convert nothing: only VACUUM (FULL, ANALYZE) the converted tables, for a run that "
                                 "committed but could not vacuum (needs the database stamped with this key)")
    args = parser.parse_args(argv)

    if not _ENV_NAME.match(args.database_url_env):
        print("--database-url-env takes the NAME of an environment variable.", file=sys.stderr)
        return 2
    url = os.environ.get(args.database_url_env)
    if not url:
        print(f"The environment variable {args.database_url_env} is not set.", file=sys.stderr)
        return 1

    key_copy = None
    if args.key_env:
        if not _ENV_NAME.match(args.key_env):
            print("--key-env takes the NAME of an environment variable.", file=sys.stderr)
            return 2
        text = os.environ.get(args.key_env, "").strip()
        if not text:
            print(f"The environment variable {args.key_env} is not set.", file=sys.stderr)
            return 1
        handle, key_copy = tempfile.mkstemp(prefix="mumu-data-key-")
        with os.fdopen(handle, "w", encoding="ascii", errors="replace") as out:
            out.write(text + "\n")
        args.key_file = key_copy
    try:
        return _with_key(args, url, modules, connect, families)
    finally:
        if key_copy is not None:
            with contextlib.suppress(OSError):
                os.remove(key_copy)


def _with_key(args, url: str, modules, connect, families) -> int:
    os.environ["DATA_KEY_FILE"] = args.key_file
    if modules is None:
        modules = {folder: load_bot_db(folder) for folder in dict.fromkeys(STAGE_FOLDER[s] for s in STAGES)
                   if folder != "family_link"}
        modules["family_link"] = load_family_link()
    try:
        fingerprints = hand_over_key(args.key_file, modules)
    except KeyProblem as exc:
        print(f"The data key cannot be used: {exc}", file=sys.stderr)
        return 1
    if len(set(fingerprints.values())) != 1:
        print("Refused: the bots' vaults do not all hold the key given, so what one sealed another could not "
              "open; nothing was changed. Fingerprints: "
              + ", ".join(f"{name} {fp or 'none'}" for name, fp in sorted(fingerprints.items())),
              file=sys.stderr)
        return 2
    fingerprint = next(iter(fingerprints.values()))
    print(f"Key fingerprint: {fingerprint}")

    if connect is None:
        import psycopg

        def connect(address):
            return psycopg.connect(address)

    try:
        conn = connect(url)
    except Exception as exc:
        print(f"Could not connect to the database named by {args.database_url_env} "
              f"({type(exc).__name__}).", file=sys.stderr)
        return 1

    if args.vacuum_only:
        mode = "vacuum only, nothing is converted"
    else:
        mode = "DRY RUN -- nothing will be kept" if args.dry_run else "for real"
    print(f"Sealing personal data, {mode}.")
    try:
        link = modules["family_link"]
        try:
            fams = families or default_families(conn)
            for family in fams:
                stamped = link.read_stamp(conn, link.DATA_KEY_SETTING, family)
                print(f"Stamped fingerprint, {family}: {stamped if stamped is not None else 'none (not stamped)'}")
            if args.vacuum_only:
                refuse_if_alive(conn, link, fams, ALIVE_WITHIN_SECONDS, "nothing was vacuumed")
                require_stamps(conn, fingerprint, link, fams)
                tables = tables_to_vacuum(conn, link, fams)
                conn.rollback()
            else:
                conn.rollback()
                tables = run(conn, fingerprint, modules, print, fams)
        except Refusal as exc:
            conn.rollback()
            print(f"Refused: {exc}", file=sys.stderr)
            return 2
        except RuntimeError as exc:             # our own: verification and layout failures
            conn.rollback()
            print(f"Failed, rolled back: {exc}", file=sys.stderr)
            return 1
        except Exception as exc:
            conn.rollback()
            # A database error can quote the row that caused it; say what kind
            # it was and nothing more.
            print(f"Failed, rolled back: {type(exc).__name__}"
                  f"{' ' + str(getattr(exc, 'sqlstate', '')) if getattr(exc, 'sqlstate', None) else ''}.",
                  file=sys.stderr)
            return 1
        if args.dry_run:
            conn.rollback()
            print("Dry run: rolled back. Nothing was changed.")
            return 0
        if not args.vacuum_only:
            conn.commit()
            print("Committed.")
        # The rows the conversion replaced, and the columns it dropped, are
        # still on disk, readable, until the table is rewritten.
        print("Vacuuming (VACUUM FULL, ANALYZE), so no readable copy stays on disk:")
        failed = 0
        try:
            conn.autocommit = True
        except Exception:                       # noqa: BLE001
            failed = len(tables) or 1
            print("  could not switch to autocommit; nothing was vacuumed")
        else:
            failed = vacuum_tables(conn, tables, print)
        if failed:
            print("Converted; vacuum incomplete: rerun with --vacuum-only", file=sys.stderr)
            return 3
        print("Done: committed. Start the bots." if not args.vacuum_only else "Done: vacuumed.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
