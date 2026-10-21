"""No database call may wait for ever -- read from the source, no database needed.

    python3 tests/test_db_timeouts.py

The incident of 2026-10-09 08:40 UTC: one query on a dead network path waited
out TCP retransmission, minutes, holding one of the eight threads that the five
bots share in the combined process. So every pool a bot builds carries, in the
`kwargs` it connects with:

    connect_timeout=10            a connection attempt gives up after 10 s
    tcp_user_timeout=30000        data nobody acknowledges: 30 s (milliseconds)
    options ... -c statement_timeout=60s
                ... -c idle_in_transaction_session_timeout=120s
                                  the server's side of the same limits, with
                                  search_path still first, as before

and the poll lease's connection (shared/lifecycle.py) -- one connection held
for the process's life, whose session-level advisory lock IS the lease -- gets
the first two and the keepalives, and deliberately neither server-side
timeout: a server that cut it for being idle would drop the lease.

This file only reads the code (with ast), so it runs anywhere, and a new pool
or a new connection somebody adds has to meet it. tests/db_timeouts.py is the
half that asks a real Postgres what a pooled connection ends up with.
"""
import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

failures = []


def check(label, ok, detail=""):
    print(("  ok   " if ok else "  FAIL ") + label + ("" if ok else "  [" + str(detail) + "]"))
    if not ok:
        failures.append(label)


SKIP_DIRS = {"tests", "scripts", "backups", "docs", ".git", "__pycache__", ".mirror", "TempTools", ".vscode",
             ".github"}

# Where psycopg.connect is called outside a pool, and why that is fine.
UNPOOLED_OK = {
    "anon_bot/db.py": "connect() and ensure_schema(): offline tools and the start-up migration",
    "sticker_bot/db.py": "connect() and ensure_schema(): offline tools and the start-up migration",
    "convert_bot/db.py": "connect() and ensure_schema(): offline tools and the start-up migration",
    "downloader_bot/db.py": "connect() and ensure_schema(): offline tools and the start-up migration",
    "manager_bot/db.py": "connect() and ensure_schema(): offline tools and the start-up migration",
    "manager_bot/family_db.py": "the backup and restore tool, run by hand",
    "migrate_to_shared_db.py": "a one-off migration, run by hand",
    "shared/lifecycle.py": "the poll lease, below",
    "testbot/switch.py": "TestBot's switch, run by hand, with its own connect_timeout",
}


def shipped_files():
    for path in sorted(REPO.rglob("*.py")):
        rel = path.relative_to(REPO)
        if any(part in SKIP_DIRS for part in rel.parts[:-1]):
            continue
        yield rel.as_posix(), path


def calls(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (isinstance(func, ast.Name) and func.id == name) or \
               (isinstance(func, ast.Attribute) and func.attr == name):
                yield node


def literal_dict(node):
    """{key: value node} for a dict literal with constant string keys, else None."""
    if not isinstance(node, ast.Dict):
        return None
    out = {}
    for key, value in zip(node.keys, node.values):
        if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
            return None
        out[key.value] = value
    return out


def const(node):
    return node.value if isinstance(node, ast.Constant) else None


def text_of(node):
    """The string an options expression builds, with {} where a value is filled in."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(part.value if isinstance(part, ast.Constant) else "{}" for part in node.values)
    return None


def main() -> int:
    parsed = {rel: ast.parse(path.read_text(encoding="utf-8")) for rel, path in shipped_files()}

    print("== every ConnectionPool ==")
    pools = [(rel, call) for rel, tree in parsed.items() for call in calls(tree, "ConnectionPool")]
    check("one pool per bot, and no other", sorted(rel for rel, _ in pools) ==
          sorted(f"{bot}/db.py" for bot in ("sticker_bot", "convert_bot", "downloader_bot", "anon_bot", "manager_bot")),
          [rel for rel, _ in pools])
    for rel, call in pools:
        kwargs_node = next((kw.value for kw in call.keywords if kw.arg == "kwargs"), None)
        settings = literal_dict(kwargs_node) if kwargs_node is not None else None
        check(f"{rel}: connects with a kwargs dict it can be read from", settings is not None)
        if settings is None:
            continue
        check(f"{rel}: connect_timeout is 10 s", const(settings.get("connect_timeout")) == 10,
              ast.unparse(settings["connect_timeout"]) if "connect_timeout" in settings else "missing")
        check(f"{rel}: tcp_user_timeout is 30 s", const(settings.get("tcp_user_timeout")) == 30000,
              ast.unparse(settings["tcp_user_timeout"]) if "tcp_user_timeout" in settings else "missing")
        options = text_of(settings["options"]) if "options" in settings else None
        check(f"{rel}: options still begin with the search_path",
              options is not None and options.startswith("-c search_path={},public"), options)
        check(f"{rel}: statement_timeout is a minute",
              options is not None and " -c statement_timeout=60s" in options, options)
        check(f"{rel}: idle_in_transaction_session_timeout is two minutes",
              options is not None and " -c idle_in_transaction_session_timeout=120s" in options, options)
        check(f"{rel}: the keepalives are still there",
              all(const(settings.get(k)) == v for k, v in (("keepalives", 1), ("keepalives_idle", 30),
                                                          ("keepalives_interval", 10), ("keepalives_count", 5))))

    print("\n== the poll lease's connection ==")
    lease = [call for call in calls(parsed["shared/lifecycle.py"], "connect")
             if isinstance(call.func, ast.Attribute) and ast.unparse(call.func) == "psycopg.connect"]
    check("there is exactly one", len(lease) == 1, len(lease))
    if len(lease) == 1:
        keywords = {kw.arg: const(kw.value) for kw in lease[0].keywords}
        check("connect_timeout is set", isinstance(keywords.get("connect_timeout"), int)
              and 0 < keywords["connect_timeout"] <= 15, keywords)
        check("tcp_user_timeout is 30 s, as in the pools", keywords.get("tcp_user_timeout") == 30000, keywords)
        check("the keepalives are the pools' ",
              all(keywords.get(k) == v for k, v in (("keepalives", 1), ("keepalives_idle", 30),
                                                    ("keepalives_interval", 10), ("keepalives_count", 5))), keywords)
        shown = ast.unparse(lease[0])
        check("it has no server-side timeout of its own: that would end the lease",
              "options" not in keywords and "statement_timeout" not in shown
              and "idle_in_transaction" not in shown, shown)
        check("it is autocommit, so it never sits in a transaction", keywords.get("autocommit") is True, keywords)

    print("\n== no other connection is made without a reason ==")
    for rel, tree in parsed.items():
        direct = [c for c in calls(tree, "connect")
                  if isinstance(c.func, ast.Attribute) and ast.unparse(c.func) == "psycopg.connect"]
        if direct:
            check(f"{rel}: psycopg.connect is expected here ({UNPOOLED_OK.get(rel, 'not listed')})",
                  rel in UNPOOLED_OK, "a new connection: give it timeouts, or list it in UNPOOLED_OK with the reason")

    print("\n== /sql keeps its own, shorter, statement timeout ==")
    source = (REPO / "manager_bot" / "db.py").read_text(encoding="utf-8")
    runner = source.split("def run_readonly_query", 1)[1].split("\ndef ", 1)[0]
    # The ten seconds is SQL_TIMEOUT_SECONDS (db.py), which the sql help page prints.
    check("/sql sets statement_timeout to ten seconds for its own transaction",
          "SET LOCAL statement_timeout = '{SQL_TIMEOUT_SECONDS}s'" in runner
          and "\nSQL_TIMEOUT_SECONDS = 10\n" in source)

    print("")
    print("FAILURES: " + str(len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
