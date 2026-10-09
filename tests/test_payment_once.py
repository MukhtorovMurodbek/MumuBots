"""Every place a real payment becomes credit passes Telegram's charge id and
copes with "already credited".

    python3 tests/test_payment_once.py

`credit_scenarios.py` proves the guard itself against a real Postgres: one payment
delivered twice -- or six times at once -- is credited once, and a crash between
the claim and the credit leaves no claim. That is only worth anything while every
caller uses it. This reads the source (as `test_pause_gate.py` does, for the same
reason: the failure is invisible in review and shows up as a person paid twice)
and asks of each call to `family_link.topup` that is not the sandbox's:

  * does it pass the payment's charge id, and
  * does what comes back get checked for None before it is used -- for ConvertBot's
    own payment handler above all, because ignoring None there would start the
    conversion a second time.

No database, no network.
"""
import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
failures = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL  ") + name + (f" -- {detail}" if not ok and detail else ""))
    if not ok:
        failures.append(name)


def topup_sites(path):
    """(line, positional args, keyword names) of every asyncio.to_thread(<x>.topup, ...) and every
    direct <x>.topup(...) call."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "to_thread" and node.args:
            target = node.args[0]
            if isinstance(target, ast.Attribute) and target.attr == "topup":
                found.append((node.lineno, node.args[1:], {k.arg for k in node.keywords}))
        elif isinstance(func, ast.Attribute) and func.attr == "topup":
            found.append((node.lineno, node.args, {k.arg for k in node.keywords}))
    return found


def main():
    print("=== family_link.topup ===")
    src = (ROOT / "shared" / "family_link.py").read_text(encoding="utf-8")
    body = src.split("def topup(", 1)[1].split("\ndef ", 1)[0]
    check("topup takes the charge id", "charge_id" in body.split(":", 1)[0] + body[:300])
    claim = body.find("stars:paid:")
    credit = body.find("_move_in(conn")
    check("the claim is staked before the credit is made", 0 < claim < credit, f"{claim} {credit}")
    check("and inside the same transaction as the credit (one `with _connect()`)", body.count("with _connect()") == 1)
    check("a claim already held returns None without crediting", "return None" in body[claim:credit])

    print("\n=== the callers ===")
    sites = []
    for rel in ("shared/shared_features.py", "convert_bot/bot.py"):
        path = ROOT / rel
        for line, args, keywords in topup_sites(path):
            detail = ast.unparse(args[2]).lower() if len(args) > 2 else ""
            sites.append((rel, line, len(args), keywords, "sandbox" in detail))
    check("the callers were found (the donation path, ConvertBot's payment, ConvertBot's sandbox)", len(sites) >= 3, str(sites))
    for rel, line, nargs, keywords, sandbox_only in sites:
        if sandbox_only and rel == "convert_bot/bot.py":
            check(f"{rel}:{line} is the sandbox: nothing is charged, so no charge id", True)
            continue
        check(f"{rel}:{line} passes the charge id", nargs >= 4 or "charge_id" in keywords, f"{nargs} args {keywords}")

    def none_branch_returns(rel, function, variable):
        """Inside `function`, is there an `if <variable> is None:` whose own body returns -- and does
        the function use `<variable>[...]` anywhere BEFORE it? (A check that is not first is no check.)"""
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == function:
                guard = None
                for child in ast.walk(node):
                    if (isinstance(child, ast.If) and isinstance(child.test, ast.Compare)
                            and isinstance(child.test.left, ast.Name) and child.test.left.id == variable
                            and len(child.test.ops) == 1 and isinstance(child.test.ops[0], ast.Is)
                            and isinstance(child.test.comparators[0], ast.Constant)
                            and child.test.comparators[0].value is None):
                        guard = child
                        break
                if guard is None:
                    return False, "no `is None` check"
                returns = any(isinstance(n, ast.Return) for b in guard.body for n in ast.walk(b))
                early = [n for n in ast.walk(node) if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name)
                         and n.value.id == variable and n.lineno < guard.lineno]
                return returns and not early, f"returns={returns} used-before={len(early)}"
        return False, f"{function} not found"

    check("_credit_topup returns from the `is None` branch, before it reads the result",
          *none_branch_returns("shared/shared_features.py", "_credit_topup", "result"))
    check("the donation handler returns from the `is None` branch, so nothing is sent",
          *none_branch_returns("shared/shared_features.py", "donation_payment_received", "text"))
    check("ConvertBot's payment handler returns from the `is None` branch -- BEFORE it can start the conversion "
          "again, and before it reads the result",
          *none_branch_returns("convert_bot/bot.py", "successful_payment_callback", "result"))


if __name__ == "__main__":
    main()
    print()
    if failures:
        print(f"FAILURES: {len(failures)}")
        for f in failures:
            print("  -", f)
        sys.exit(1)
    print("Every payment path credits once.")
