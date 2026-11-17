#!/usr/bin/env python3
"""Replace the data key: every seal and every pseudonym in the database goes from the"""
from __future__ import annotations

import argparse
import base64
import binascii
import importlib.util
import json
import os
import sys
from pathlib import Path

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[2]
VAULT_PATH = ROOT / "shared" / "security" / "vault.py"
OFFLINE_KEY_PATH = ROOT / "shared" / "security" / "offline_key.py"

PAYER_DOMAIN = "anon.payer"
STATE_PURPOSE = "lifecycle.state"
WAITLIST_PURPOSE = "lifecycle.waitlist"
ERASURE_PURPOSE = "reserve.erasure"
INVITE_PURPOSE = "sticker.invite.username"
INVITE_MARK_DOMAIN = "sticker.invite"

PLAIN_ID_COLUMNS = ("user_id", "chat_id", "requested_by", "owner_user_id", "follower_user_id")

PSEUDONYM_ONLY = (
    ("anon_follower_state", ("follower_user_id", "owner_user_id")),
    ("anon_relay", ("chat_id",)),
    ("anon_blocks", ("owner_user_id", "follower_user_id")),
    ("donation_prompts", ("user_id",)),
    ("promo_seen", ("user_id",)),
    ("activity_events", ("user_id",)),
)
SEALED_IDS = (
    ("user_settings", "user_id", [("user_id", "user_sealed", "anon.user")]),
    ("anon_links", "owner_user_id", [("owner_user_id", "owner_sealed", "anon.link.owner")]),
    ("anon_conversations", "id", [("owner_user_id", "owner_sealed", "anon.conversation.owner"),
                                  ("follower_user_id", "follower_sealed", "anon.conversation.follower")]),
)
SEALED_VALUES = (
    ("star_transactions", "username_sealed", "stars.username"),
    ("packs", "owner_sealed", "sticker.pack.owner"),
)


class Refusal(Exception):
    pass


def _load_by_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def keyring(name: str, key_file: str):
    os.environ.pop("DATA_KEY_INIT", None)
    vault = _load_by_path(name, VAULT_PATH)
    vault.KEY_FILE = str(key_file)
    vault._keys = None
    vault.require()
    return vault


def _encrypt_script():
    return _load_by_path("encrypt_personal_data", ROOT / "scripts" / "security" / "encrypt_personal_data.py")


def _q(schema: str, table: str):
    return sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(table))


def _schemas_with(conn, table: str, *columns: str) -> "list[str]":
    rows = conn.execute(
        "SELECT table_schema FROM information_schema.columns WHERE table_name = %s "
        "AND column_name = ANY(%s) GROUP BY table_schema HAVING count(*) = %s ORDER BY 1",
        (table, list(columns) or ["?"], len(columns)),
    ).fetchall() if columns else conn.execute(
        "SELECT table_schema FROM information_schema.tables WHERE table_name = %s ORDER BY 1", (table,)).fetchall()
    return [row[0] for row in rows]


class Rotation:
    def __init__(self, conn, old, new, say=print):
        self.conn, self.old, self.new, self.say = conn, old, new, say
        self.people: "set[int]" = set()
        self.counts: "dict[str, int]" = {}
        self.touched: "set[tuple[str, str]]" = set()
        self._pseudo: "dict[int, int]" = {}
        self._payer: "dict[int, int]" = {}

    def count(self, label: str, n: int) -> None:
        if n:
            self.counts[label] = self.counts.get(label, 0) + n

    def learn(self, real) -> None:
        real = int(real)
        if not self.old.is_pseudonym(real):
            self.people.add(real)

    def open_id(self, blob, purpose: str, expected: int) -> int:
        return self.old.open_id(bytes(blob), purpose, expected=expected)

    def learn_everyone(self, anon_schemas, state_schemas, waitlist_schemas) -> None:
        conn = self.conn
        for schema in anon_schemas:
            for table, _, columns in SEALED_IDS:
                for pseudo_col, sealed_col, purpose in columns:
                    rows = conn.execute(sql.SQL("SELECT {}, {} FROM {} WHERE {} IS NOT NULL").format(
                        sql.Identifier(pseudo_col), sql.Identifier(sealed_col), _q(schema, table),
                        sql.Identifier(sealed_col))).fetchall()
                    for pseudo, sealed in rows:
                        self.learn(self.open_id(sealed, purpose, pseudo))
        for schema in waitlist_schemas:
            for (sealed,) in conn.execute(sql.SQL("SELECT who_sealed FROM {} WHERE who_sealed IS NOT NULL")
                                          .format(_q(schema, "update_waitlist"))).fetchall():
                try:
                    with_who = self.old.open_json(bytes(sealed), WAITLIST_PURPOSE)
                    self.learn(with_who["user"])
                    self.learn(with_who["chat"])
                except (KeyError, TypeError, ValueError, self.old.VaultError):
                    continue
        for schema in state_schemas:
            for kind, _, value in self._state_rows(schema):
                payload = self._open_state(value)
                if payload is None:
                    continue
                try:
                    parts = self._state_ids(kind, payload["key"])
                except (TypeError, ValueError):
                    continue
                for part in parts:
                    self.learn(part)
        for schema, table, column in conn.execute(
                "SELECT c.table_schema, c.table_name, c.column_name FROM information_schema.columns c "
                "JOIN information_schema.tables t ON t.table_schema = c.table_schema AND t.table_name = c.table_name "
                "WHERE t.table_type = 'BASE TABLE' AND c.data_type = 'bigint' AND c.column_name = ANY(%s) "
                "AND c.table_schema NOT IN ('pg_catalog', 'information_schema') ORDER BY 1, 2, 3",
                (list(PLAIN_ID_COLUMNS),)).fetchall():
            for (value,) in conn.execute(sql.SQL("SELECT DISTINCT {} FROM {} WHERE {} >= %s AND {} <> 0").format(
                    sql.Identifier(column), _q(schema, table), sql.Identifier(column), sql.Identifier(column)),
                    (self.old.PSEUDONYM_CEILING,)).fetchall():
                self.learn(value)
        self._pseudo = {self.old.pseudonym(p): self.new.pseudonym(p) for p in self.people}
        self._payer = {self.old.pseudonym_in(p, PAYER_DOMAIN): self.new.pseudonym_in(p, PAYER_DOMAIN)
                       for p in self.people}
        self.say(f"  people the database can name: {len(self.people)}")

    def new_pseudonym(self, old_pseudonym) -> "int | None":
        return self._pseudo.get(int(old_pseudonym))

    def rekey_sealed_ids(self, schema: str) -> None:
        conn = self.conn
        for table, row_key, columns in SEALED_IDS:
            if schema not in _schemas_with(conn, table):
                continue
            selected = [sql.Identifier(row_key)]
            for pseudo_col, sealed_col, _ in columns:
                selected += [sql.Identifier(pseudo_col), sql.Identifier(sealed_col)]
            rows = conn.execute(sql.SQL("SELECT {} FROM {}").format(sql.SQL(", ").join(selected),
                                                                    _q(schema, table))).fetchall()
            for row in rows:
                assignments, values, at = [], [], 1
                for pseudo_col, sealed_col, purpose in columns:
                    pseudo, sealed = row[at], row[at + 1]
                    at += 2
                    if sealed is None:
                        moved = self.new_pseudonym(pseudo) if self.old.is_pseudonym(pseudo) else None
                        if moved is None:
                            raise RuntimeError(f"{schema}.{table}: a row has neither a seal nor a person "
                                               f"this run can name in {pseudo_col}")
                        assignments.append(sql.SQL("{} = %s").format(sql.Identifier(pseudo_col)))
                        values.append(moved)
                        continue
                    real = self.open_id(sealed, purpose, pseudo)
                    assignments += [sql.SQL("{} = %s").format(sql.Identifier(pseudo_col)),
                                    sql.SQL("{} = %s").format(sql.Identifier(sealed_col))]
                    values += [self.new.pseudonym(real), self.new.seal_id(real, purpose)]
                cur = conn.execute(sql.SQL("UPDATE {} SET {} WHERE {} = %s").format(
                    _q(schema, table), sql.SQL(", ").join(assignments), sql.Identifier(row_key)),
                    values + [row[0]])
                if cur.rowcount != 1:
                    raise RuntimeError(f"{schema}.{table}: expected to change one row, changed {cur.rowcount}")
            self.count(f"{schema}.{table}", len(rows))
            self.touched.add((schema, table))

    def rekey_pseudonym_only(self, schema: str) -> None:
        conn = self.conn
        for table, columns in PSEUDONYM_ONLY:
            if schema not in _schemas_with(conn, table, *columns):
                continue
            mapping_old, mapping_new = [], []
            seen: set = set()
            for column in columns:
                for (value,) in conn.execute(sql.SQL("SELECT DISTINCT {} FROM {}").format(
                        sql.Identifier(column), _q(schema, table))).fetchall():
                    if value is None or value in seen:
                        continue
                    seen.add(value)
                    moved = self.new_pseudonym(value)
                    if moved is not None:
                        mapping_old.append(value)
                        mapping_new.append(moved)
            unexplained = sql.SQL(" OR ").join(
                sql.SQL("{} <> ALL(%s::bigint[])").format(sql.Identifier(column)) for column in columns)
            gone = conn.execute(sql.SQL("DELETE FROM {} WHERE {}").format(_q(schema, table), unexplained),
                                [mapping_old] * len(columns)).rowcount or 0
            self.count(f"{schema}.{table} (deleted: nobody this run can name)", gone)
            changed = 0
            for column in columns:
                changed += conn.execute(sql.SQL(
                    "UPDATE {} t SET {} = m.new FROM unnest(%s::bigint[], %s::bigint[]) AS m(old, new) "
                    "WHERE t.{} = m.old").format(_q(schema, table), sql.Identifier(column), sql.Identifier(column)),
                    (mapping_old, mapping_new)).rowcount or 0
            self.count(f"{schema}.{table}", changed)
            self.touched.add((schema, table))

    def rekey_payers(self, schema: str) -> None:
        conn = self.conn
        if schema not in _schemas_with(conn, "star_transactions", "user_id"):
            return
        stand_ins = [row[0] for row in conn.execute(sql.SQL(
            "SELECT DISTINCT user_id FROM {} WHERE user_id < %s").format(_q(schema, "star_transactions")),
            (self.old.PSEUDONYM_CEILING,)).fetchall()]
        known = [s for s in stand_ins if s in self._payer]
        changed = conn.execute(sql.SQL(
            "UPDATE {} t SET user_id = m.new FROM unnest(%s::bigint[], %s::bigint[]) AS m(old, new) "
            "WHERE t.user_id = m.old").format(_q(schema, "star_transactions")),
            (known, [self._payer[s] for s in known])).rowcount or 0
        self.count(f"{schema}.star_transactions (payers)", changed)
        unknown = len(stand_ins) - len(known)
        self.count(f"{schema}.star_transactions (payers nobody can name: kept as they were)", unknown)
        self.touched.add((schema, "star_transactions"))

    def reseal(self, blob, purpose: str) -> bytes:
        return self.new.seal(self.old.unseal(bytes(blob), purpose), purpose)

    def rekey_values(self) -> None:
        conn = self.conn
        for table, column, purpose in SEALED_VALUES:
            for schema in _schemas_with(conn, table, column, "id"):
                rows = conn.execute(sql.SQL("SELECT id, {} FROM {} WHERE {} IS NOT NULL").format(
                    sql.Identifier(column), _q(schema, table), sql.Identifier(column))).fetchall()
                if rows:
                    conn.execute(sql.SQL(
                        "UPDATE {} t SET {} = m.new FROM unnest(%s::bigint[], %s::bytea[]) AS m(id, new) "
                        "WHERE t.id = m.id").format(_q(schema, table), sql.Identifier(column)),
                        ([r[0] for r in rows], [self.reseal(r[1], purpose) for r in rows]))
                self.count(f"{schema}.{table}.{column}", len(rows))
                self.touched.add((schema, table))
        for schema in _schemas_with(conn, "pack_invites", "username_mark", "username_sealed"):
            rows = conn.execute(sql.SQL("SELECT pack_name, username_mark, username_sealed FROM {}").format(
                _q(schema, "pack_invites"))).fetchall()
            for pack_name, mark, sealed in rows:
                if sealed is None:
                    raise RuntimeError(f"{schema}.pack_invites: an invitation has no sealed name, so its "
                                       f"keyed hash cannot be made again")
                name = self.old.unseal(bytes(sealed), INVITE_PURPOSE).decode("utf-8")
                if self.old.text_mark(name.lstrip("@").lower(), INVITE_MARK_DOMAIN) != mark:
                    raise RuntimeError(f"{schema}.pack_invites: an invitation's hash does not match its name")
                conn.execute(sql.SQL(
                    "UPDATE {} SET username_mark = %s, username_sealed = %s "
                    "WHERE pack_name = %s AND username_mark = %s").format(_q(schema, "pack_invites")),
                    (self.new.text_mark(name.lstrip("@").lower(), INVITE_MARK_DOMAIN),
                     self.new.seal(name.encode("utf-8"), INVITE_PURPOSE), pack_name, mark))
            self.count(f"{schema}.pack_invites", len(rows))
            self.touched.add((schema, "pack_invites"))

    def _state_rows(self, schema: str):
        return self.conn.execute(sql.SQL("SELECT kind, key, value FROM {}").format(
            _q(schema, "runtime_state"))).fetchall()

    def _open_state(self, value) -> "dict | None":
        try:
            blob = base64.b64decode(value["sealed"], validate=True)
            payload = json.loads(self.old.unseal(blob, STATE_PURPOSE).decode("utf-8"))
            payload["key"], payload["_blob"] = str(payload["key"]), blob
            return payload
        except (KeyError, TypeError, ValueError, binascii.Error, self.old.VaultError):
            return None

    @staticmethod
    def _state_ids(kind: str, key: str) -> "list[int]":
        if kind == "user":
            return [int(key)]
        if kind == "conversation":
            return [int(part) for part in key.partition("|")[2].split(":")]
        raise ValueError(kind)

    def _state_key(self, vault, kind: str, key: str) -> str:
        if kind == "user":
            return str(vault.pseudonym(int(key)))
        name, _, pair = key.partition("|")
        return f"{name}|" + ":".join(str(vault.pseudonym(int(part))) for part in pair.split(":"))

    def rekey_state(self, schema: str) -> None:
        conn = self.conn
        changed = gone = 0
        for kind, stored_key, value in self._state_rows(schema):
            payload = self._open_state(value)
            belongs = False
            if payload is not None:
                try:
                    belongs = self._state_key(self.old, kind, payload["key"]) == stored_key
                except (ValueError, TypeError):
                    belongs = False
            if not belongs:
                gone += conn.execute(sql.SQL("DELETE FROM {} WHERE kind = %s AND key = %s").format(
                    _q(schema, "runtime_state")), (kind, stored_key)).rowcount or 0
                continue
            sealed = self.new.seal(self.old.unseal(payload["_blob"], STATE_PURPOSE), STATE_PURPOSE)
            conn.execute(sql.SQL("UPDATE {} SET key = %s, value = %s::jsonb WHERE kind = %s AND key = %s").format(
                _q(schema, "runtime_state")),
                (self._state_key(self.new, kind, payload["key"]),
                 json.dumps({"sealed": base64.b64encode(sealed).decode("ascii")}), kind, stored_key))
            changed += 1
        self.count(f"{schema}.runtime_state", changed)
        self.count(f"{schema}.runtime_state (deleted: did not open)", gone)
        self.touched.add((schema, "runtime_state"))

    def rekey_waitlist(self, schema: str) -> None:
        conn = self.conn
        rows = conn.execute(sql.SQL("SELECT bot_id, user_id, chat_id, who_sealed FROM {}").format(
            _q(schema, "update_waitlist"))).fetchall()
        changed = gone = 0
        for bot_id, user_pseudo, chat_pseudo, sealed in rows:
            try:
                who = self.old.open_json(bytes(sealed), WAITLIST_PURPOSE)
                user, chat = int(who["user"]), int(who["chat"])
                ok = self.old.pseudonym(user) == user_pseudo and self.old.pseudonym(chat) == chat_pseudo
            except (TypeError, KeyError, ValueError, self.old.VaultError):
                ok = False
            if not ok:
                gone += conn.execute(sql.SQL("DELETE FROM {} WHERE bot_id = %s AND user_id = %s").format(
                    _q(schema, "update_waitlist")), (bot_id, user_pseudo)).rowcount or 0
                continue
            conn.execute(sql.SQL("UPDATE {} SET user_id = %s, chat_id = %s, who_sealed = %s "
                                 "WHERE bot_id = %s AND user_id = %s").format(_q(schema, "update_waitlist")),
                         (self.new.pseudonym(user), self.new.pseudonym(chat),
                          self.new.seal(self.old.unseal(bytes(sealed), WAITLIST_PURPOSE), WAITLIST_PURPOSE),
                          bot_id, user_pseudo))
            changed += 1
        self.count(f"{schema}.update_waitlist", changed)
        self.count(f"{schema}.update_waitlist (deleted: did not open)", gone)
        self.touched.add((schema, "update_waitlist"))

    def rekey_erasures(self, schema: str, offline_key) -> None:
        conn = self.conn
        rows = conn.execute(sql.SQL("SELECT id, sealed_user FROM {}").format(_q(schema, "erasures"))).fetchall()
        sealed = [(row_id, blob) for row_id, blob in rows if not offline_key.is_encrypted(bytes(blob))]
        if sealed:
            conn.execute(sql.SQL(
                "UPDATE {} t SET sealed_user = m.new FROM unnest(%s::bigint[], %s::bytea[]) AS m(id, new) "
                "WHERE t.id = m.id").format(_q(schema, "erasures")),
                ([r[0] for r in sealed], [self.reseal(r[1], ERASURE_PURPOSE) for r in sealed]))
        self.count(f"{schema}.erasures", len(sealed))
        self.count(f"{schema}.erasures (for the offline key: left as they are)", len(rows) - len(sealed))
        self.touched.add((schema, "erasures"))

    def no_old_pseudonyms_left(self, anon_schemas, waitlist_schemas) -> None:
        olds = list(self._pseudo)
        problems = []
        for schema in anon_schemas + waitlist_schemas:
            for table, column in self.conn.execute(
                    "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = %s "
                    "AND data_type = 'bigint' AND column_name = ANY(%s) ORDER BY 1, 2",
                    (schema, ["user_id", "chat_id", "owner_user_id", "follower_user_id"])).fetchall():
                if table == "star_transactions":
                    continue
                left = self.conn.execute(sql.SQL("SELECT count(*) FROM {} WHERE {} = ANY(%s::bigint[])").format(
                    _q(schema, table), sql.Identifier(column)), (olds,)).fetchone()[0]
                if left:
                    problems.append(f"{schema}.{table}.{column}: {left} row(s)")
        if problems:
            raise RuntimeError("old pseudonyms are left in: " + "; ".join(problems))


def run(conn, old, new, script, say=print, families: "list[str] | None" = None) -> "list[tuple[str, str]]":
    link = script.load_family_link()
    fams = families or script.default_families(conn)
    try:
        script.refuse_if_alive(conn, link, fams, script.ALIVE_WITHIN_SECONDS, "nothing was changed")
    except script.Refusal as exc:
        raise Refusal(str(exc)) from None
    old_print, new_print = old.fingerprint(), new.fingerprint()
    if old_print == new_print:
        raise Refusal("the new key is the old key; nothing was changed")
    for family in fams:
        stamped = link.read_stamp(conn, link.DATA_KEY_SETTING, family)
        if stamped != old_print:
            raise Refusal(f"{family} is {'not stamped' if stamped is None else 'stamped with another key'}: "
                          f"--key-file must be the key this database is sealed with; nothing was changed")
        for stage in script.STAGES:
            if link.read_stamp(conn, link.sealed_setting(stage), family) != "1":
                raise Refusal(f"{family} is not fully sealed yet (stage {stage}): run "
                              f"encrypt_personal_data.py first; nothing was changed")

    rotation = Rotation(conn, old, new, say)
    anon = _schemas_with(conn, "anon_conversations")
    states = _schemas_with(conn, "runtime_state", "kind", "key", "value")
    waitlists = _schemas_with(conn, "update_waitlist", "who_sealed")
    say(f"Rotating {old_print} -> {new_print}")
    rotation.learn_everyone(anon, states, waitlists)
    for schema in anon:
        rotation.rekey_sealed_ids(schema)
        rotation.rekey_pseudonym_only(schema)
        rotation.rekey_payers(schema)
    rotation.rekey_values()
    for schema in states:
        rotation.rekey_state(schema)
    for schema in waitlists:
        rotation.rekey_waitlist(schema)
    offline_key = _load_by_path("rotate_offline_key", OFFLINE_KEY_PATH)
    for schema in _schemas_with(conn, "erasures", "sealed_user"):
        rotation.rekey_erasures(schema, offline_key)
    for family in fams:
        conn.execute(sql.SQL("UPDATE {} SET value = %s WHERE key = %s AND value = %s").format(
            _q(family, "settings")), (new_print, link.DATA_KEY_SETTING, old_print))
    for label, number in sorted(rotation.counts.items()):
        say(f"  {label}: {number}")

    script.hand_over_key(str(new.KEY_FILE), {})
    rotation.no_old_pseudonyms_left(anon, waitlists)
    anon_db = script.load_bot_db("anon_bot")
    for schema in anon:
        anon_db.verify_personal_data_v1(conn, schema)
    for family in fams:
        link.verify_personal_data_v1(conn, family)
    for schema in sorted({s for t in ("star_transactions", "packs") for s in _schemas_with(conn, t)}):
        link.verify_names_v1(conn, schema)
    say("  every check passed under the new key")
    return sorted(rotation.touched)


def _new_key_file(path: Path, vault) -> bool:
    if path.exists():
        return False
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(vault.new_key() + "\n")
    return True


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if any("://" in arg for arg in argv):
        print("The database address is never taken as an argument. Put it in an environment "
              "variable and give --database-url-env that variable's NAME.", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--database-url-env", required=True, metavar="NAME")
    parser.add_argument("--key-file", required=True, type=Path, help="the key the database is sealed with now")
    parser.add_argument("--new-key-file", required=True, type=Path, help="the new key; made if it does not exist")
    parser.add_argument("--dry-run", action="store_true", help="do everything, then roll back")
    args = parser.parse_args(argv)
    url = os.environ.get(args.database_url_env)
    if not url:
        print(f"The environment variable {args.database_url_env} is not set.", file=sys.stderr)
        return 1
    os.environ["DATABASE_URL"] = url
    os.environ["DATA_KEY_FILE"] = str(args.key_file)
    try:
        old = keyring("rotate_vault_old", args.key_file)
        if _new_key_file(args.new_key_file, old):
            print(f"Made {args.new_key_file}. Save it in a password manager now.")
        new = keyring("rotate_vault_new", args.new_key_file)
        script = _encrypt_script()
        with psycopg.connect(url) as conn:
            try:
                tables = run(conn, old, new, script)
            except BaseException:
                conn.rollback()
                raise
            if args.dry_run:
                conn.rollback()
                print("Dry run: everything worked, and nothing was changed.")
                return 0
            conn.commit()
        print(f"Committed. The database is sealed with {new.fingerprint()}; vacuuming:")
        with psycopg.connect(url, autocommit=True) as conn:
            failed = script.vacuum_tables(conn, tables, print)
        if failed:
            return 3
        print("Done. Put the new key on every volume, then start the bots (private/runbooks/data-protection.md).")
        return 0
    except Refusal as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Failed, nothing was changed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
