"""No SQL statement a user can reach is built out of anything a user sends.

The rule the code follows is that values travel as `%s` parameters, which
psycopg escapes, and the only thing ever interpolated into a statement string
is an identifier the code chose itself: a schema name from the environment, a
table name from a module constant, a column name from a literal tuple this
file can see. That rule is worth asserting rather than trusting, because it is
one careless f-string away from being untrue and the failure is invisible in
review -- `f"... WHERE name = '{name}'"` looks exactly like the safe ones.

How it works: every `.execute()` / `.executemany()` call in every bot is found
by parsing the source, and its statement argument is classified.

  a plain string literal                                       -> safe
  an f-string whose every hole is an ALL_CAPS module constant  -> safe
  a name bound by `for x in ("literal", "tuple")`              -> safe
  a name assigned from any of the above in the same function   -> safe
  a name assigned from a call listed in TRUSTED_SOURCES        -> safe
  anything else                                                -> reported

Anything reported has to be listed in ALLOWED with a reason, the same way
test_menu.py's UNADVERTISED works: "it is in the exception list" is not an
argument, so each entry has to say why it is not reachable from a message.

What this does NOT claim: that ManagerBot's `/sql` is safe to hand to a
stranger. It runs whatever the owner types, on purpose. What makes that
acceptable is the admin gate and a genuinely READ ONLY transaction, both
checked below, not anything about the statement.

Reads source rather than importing, so it needs no database and no stubs.

    python3 tests/test_sql_safety.py
"""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
BOTS = ["sticker_bot", "convert_bot", "downloader_bot", "anon_bot", "manager_bot"]

# Helpers whose return value is an identifier the code chose, not input.
TRUSTED_SOURCES = {
    # family_link.FAMILY_SCHEMA, i.e. an env var read once at import.
    "_family_schema",
    # Filters the schema names it is given against information_schema, so
    # what comes back is a list of schemas that demonstrably exist.
    "_existing_tables",
}

# Reported, and allowed anyway. Every entry needs a reason.
ALLOWED = {
    "manager_bot/db.py":
        "run_readonly_query() is ManagerBot's /sql: arbitrary SQL on purpose, "
        "behind guard() and inside a SET TRANSACTION READ ONLY, both asserted "
        "below. The UNION builders interpolate schema names that came back "
        "from information_schema, quoted.",
    "manager_bot/family_db.py":
        "the offline backup/restore tool. It is not imported by any bot and "
        "no Telegram command reaches it; the identifiers come from a backup "
        "manifest the owner chose to restore, on a machine they are sitting "
        "at.",
    "scripts/rename_to_managerbot.py":
        "the one-time ParentBot -> ManagerBot migration. Offline, run by the "
        "owner at a keyboard; no bot imports it and no Telegram command "
        "reaches it. The one interpolated identifier is a table name that "
        "came back from information_schema a few lines earlier.",
    "sticker_bot/db_merge.py": "offline merge tool, identifiers from the database catalog",
    "convert_bot/db_merge.py": "offline merge tool, identifiers from the database catalog",
    "downloader_bot/db_merge.py": "offline merge tool, identifiers from the database catalog",
    "anon_bot/db_merge.py": "offline merge tool, identifiers from the database catalog",
}

failures = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL  ") + name + (f" -- {detail}" if not ok and detail else ""))
    if not ok:
        failures.append(name)


class Auditor(ast.NodeVisitor):
    def __init__(self, tree):
        self.suspicious = []
        self.safe_names = {
            target.id
            for node in tree.body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name) and target.id.isupper()
        }
        # `for name in ("a", "b")` / `for a, b in (("x", ()),)` anywhere: the
        # value is written in this file, so a user cannot reach it.
        for node in ast.walk(tree):
            if isinstance(node, ast.For) and self._is_literal_seq(node.iter):
                for inner in ast.walk(node.target):
                    if isinstance(inner, ast.Name):
                        self.safe_names.add(inner.id)
        # Two passes over assignments, so `sql = <safe>` then `sql += <safe>`
        # is recognised whichever order the walk happens to reach them in.
        for _ in range(2):
            for node in ast.walk(tree):
                if isinstance(node, (ast.Assign, ast.AugAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        if isinstance(target, ast.Name) and self._classify(node.value) is None:
                            self.safe_names.add(target.id)

    def _is_literal_seq(self, node):
        """What a `for` may iterate and still bind provably-ours names: a
        literal tuple or list, or an ALL_CAPS module constant -- including
        `.items()` on one, which is how the release script walks its
        bot-id-to-schema map."""
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") in ("items", "keys", "values"):
            node = node.func.value
        if isinstance(node, ast.Name):
            return node.id in self.safe_names
        if not isinstance(node, (ast.Tuple, ast.List)):
            return False
        for element in node.elts:
            if isinstance(element, ast.Constant) and isinstance(element.value, str):
                continue
            if isinstance(element, (ast.Tuple, ast.List)):
                continue
            return False
        return True

    def _hole_is_safe(self, node):
        if isinstance(node, ast.Name):
            return node.id in self.safe_names
        if isinstance(node, ast.Attribute):
            return node.attr.isupper()          # family_link.FAMILY_SCHEMA
        if isinstance(node, ast.Constant):
            return True
        if isinstance(node, ast.Call):
            return getattr(node.func, "id", getattr(node.func, "attr", "")) in TRUSTED_SOURCES
        if isinstance(node, ast.IfExp):
            return self._hole_is_safe(node.body) and self._hole_is_safe(node.orelse)
        return False

    def _classify(self, node):
        """None when the statement is provably ours, else why not."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return None
        if isinstance(node, ast.JoinedStr):
            bad = [ast.unparse(v.value) for v in node.values
                   if isinstance(v, ast.FormattedValue) and not self._hole_is_safe(v.value)]
            return f"f-string interpolates {bad[0]}" if bad else None
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return self._classify(node.left) or self._classify(node.right)
        if isinstance(node, ast.IfExp):
            return self._classify(node.body) or self._classify(node.orelse)
        if isinstance(node, ast.Name):
            return None if node.id in self.safe_names else f"the variable {node.id!r}"
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", getattr(node.func, "attr", ""))
            if name in TRUSTED_SOURCES:
                return None
            return f"the result of {name or 'a call'}()"
        return f"a {type(node).__name__}"

    def visit_Call(self, node):
        if getattr(node.func, "attr", None) in ("execute", "executemany") and node.args:
            why = self._classify(node.args[0])
            if why:
                self.suspicious.append((node.lineno, why))
        self.generic_visit(node)


def main() -> int:
    print("=== no SQL statement is built out of anything a user sends ===")
    statements = 0
    for folder in BOTS + ["scripts"]:
        directory = ROOT / folder
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.py")):
            rel = f"{folder}/{path.name}"
            tree = ast.parse(path.read_text(encoding="utf-8"))
            auditor = Auditor(tree)
            auditor.visit(tree)
            statements += sum(
                1 for n in ast.walk(tree)
                if isinstance(n, ast.Call)
                and getattr(n.func, "attr", None) in ("execute", "executemany")
            )
            if rel in ALLOWED:
                # Still printed, so the exception stays visible rather than
                # becoming a place things quietly accumulate.
                print(f"  --  {rel}: {len(auditor.suspicious)} allowed ({ALLOWED[rel]})")
                continue
            check(rel, not auditor.suspicious,
                  "; ".join(f"line {line}: {why}" for line, why in auditor.suspicious))

    check(f"there were statements to check ({statements})", statements > 100, f"only {statements}")

    # A blunter second pass, over every file including the allowed ones: no
    # statement anywhere quotes an interpolation. That is the shape the actual
    # bug takes -- `'{value}'` rather than `%s` -- and no legitimate identifier
    # interpolation needs single quotes around it.
    for folder in BOTS + ["scripts"]:
        directory = ROOT / folder
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.py")):
            bad = []
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "'{" not in line or "}'" not in line:
                    continue
                if "{}" in line or "{{" in line:      # a JSON literal, not a hole
                    continue
                if any(word in line.upper() for word in
                       ("SELECT ", "INSERT ", "UPDATE ", "DELETE ", "WHERE ", "VALUES ")):
                    bad.append(f"line {number}: {line.strip()}")
            check(f"{folder}/{path.name}: no quoted hole in SQL", not bad, bad[0] if bad else "")

    # What actually makes ManagerBot's /sql acceptable.
    manager = (ROOT / "manager_bot" / "bot.py").read_text(encoding="utf-8")
    body = manager.split("async def sql_command", 1)[1].split("\nasync def ", 1)[0]
    check("/sql is behind the admin gate", "await guard(update, context)" in body)
    db = (ROOT / "manager_bot" / "db.py").read_text(encoding="utf-8")
    runner = db.split("def run_readonly_query", 1)[1].split("\ndef ", 1)[0]
    check("/sql runs in a read-only transaction", 'SET TRANSACTION READ ONLY' in runner)
    check("/sql cannot hold a connection open", "statement_timeout" in runner)
    # A read-only transaction ends with a COMMIT, and a query without
    # parameters goes over the simple protocol, which runs every statement in
    # the string: "SELECT 1; COMMIT; DROP TABLE x" left read-only after its
    # second statement. The extended protocol takes one statement, and
    # Postgres is the one to refuse the rest.
    executes = [line for line in runner.splitlines() if "conn.execute(sql" in line]
    check("/sql sends the query over the extended protocol, which takes one statement",
          bool(executes) and all("prepare=True" in line for line in executes),
          "; ".join(line.strip() for line in executes) or "no conn.execute(sql ...) found")

    print("")
    print("FAILURES: " + str(len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
