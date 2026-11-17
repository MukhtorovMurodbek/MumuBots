#!/usr/bin/env python3
"""Seal the personal data a database written by 2.1.1 (or earlier) holds in the"""
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

ROOT = Path(__file__).resolve().parents[2]

_BOTS_REGISTERED = False
_VAULT_FOLDERS: "list[str]" = []


def _bot_modules_ready() -> None:
    global _BOTS_REGISTERED
    if _BOTS_REGISTERED:
        return
    main = sys.modules.get("main")
    if main is None or getattr(main, "ROOT", None) != ROOT:
        spec = importlib.util.spec_from_file_location("main", ROOT / "main.py")
        main = importlib.util.module_from_spec(spec)
        sys.modules["main"] = main
        spec.loader.exec_module(main)
    for bot in main.discover()[0]:
        main.register(bot)
        if "vault" in bot.shared or "vault" in bot.files:
            _VAULT_FOLDERS.append(bot.folder)
    sys.meta_path.insert(0, main._BotFinder)
    _BOTS_REGISTERED = True


def bot_vaults() -> list:
    _bot_modules_ready()
    return [importlib.import_module(f"{folder}.vault") for folder in _VAULT_FOLDERS]


class KeyProblem(Exception):
    pass


def hand_over_key(key_file: str, modules: dict) -> "dict[str, str]":
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

ALIVE_WITHIN_SECONDS = 180


class Refusal(Exception):
    pass


def load_bot_db(folder: str):
    _bot_modules_ready()
    return importlib.import_module(f"{folder}.db")


def load_family_link():
    _bot_modules_ready()
    return importlib.import_module(f"{STAGE_FOLDER[STAGES[0]]}.family_link")


def default_families(conn) -> "list[str]":
    names = {row[0] for row in conn.execute(
        "SELECT DISTINCT schemaname FROM pg_catalog.pg_tables "
        "WHERE schemaname <> 'information_schema' AND schemaname NOT LIKE 'pg\\_%'").fetchall()}
    families = [REAL_FAMILY]
    if any(name.startswith("testbot_") for name in names):
        families.append(TEST_FAMILY)
    return families


def flatten(result) -> "list[tuple[str, object]]":
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
    found: set = set()
    for family in fams:
        found |= set(link.personal_data_tables(conn, tuple(STAGES), family))
    return sorted(found)


def _filenode(conn, schema: str, table: str):
    row = conn.execute(
        "SELECT pg_relation_filenode(c.oid) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = %s AND c.relname = %s", (schema, table)).fetchone()
    return row[0] if row else None


def vacuum_tables(conn, tables: "list[tuple[str, str]]", say) -> int:
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
    alive = [bot for family in fams for bot in link.fresh_heartbeats(conn, alive_within, family)]
    if alive:
        raise Refusal("these bots look alive (a heartbeat in the last "
                      f"{alive_within // 60} minutes): {', '.join(sorted(set(alive)))}. "
                      f"Stop them, wait, and run this again; {nothing_done}")


def require_stamps(conn, fingerprint: str, link, fams: "list[str]") -> None:
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
    link = modules["family_link"]

    missing = [stage for stage in link.SEAL_STAGES if stage not in STAGES]
    if missing:
        raise Refusal(f"the bots require stage(s) {', '.join(missing)} that this script does not convert")

    conn.execute("SET LOCAL lock_timeout = '60s'")
    conn.execute("SET LOCAL statement_timeout = '600s'")

    fams = families or default_families(conn)

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
        except RuntimeError as exc:
            conn.rollback()
            print(f"Failed, rolled back: {exc}", file=sys.stderr)
            return 1
        except Exception as exc:
            conn.rollback()
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
