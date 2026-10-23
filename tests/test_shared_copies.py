"""The modules every bot shares: stored once, and never shadowed.

Until 2.0.0 four modules were duplicated byte-for-byte into every bot --
`family_link.py`, `live_message.py`, `lifecycle.py`, `problems.py`, and
`shared_features.py` in the four public ones -- because each bot was its own
repository and deployment. This test held them to being copies. Since 2.0.0
they live once, in shared/ (the owner: "make all base bots share specific
functions"), with `big_files.py`, `envfiles.py` and `pricing.py` beside them, and a bot finds
them there when it starts.

So the risk has turned round. A copy cannot drift from the others any more;
what can happen is a file of the same name reappearing in a bot's folder --
left behind, or brought back by an old habit -- and quietly winning over the
shared one, because a bot's own module comes first. A fix to shared/ would
then miss that one bot, exactly the failure the copies used to have. So: the
shared modules are all in shared/, and no bot folder has one of its own.

THE FIFTH COPY
    ManagerBot has no `shared_features.py` -- it is private, and has no
    donations, no /cancel, no translations. What it does have is
    `monitoring.py`, which holds the logging and status half of that file:
    22 functions, 18 of them character-for-character identical to their
    counterparts.

    Those 18 are not covered by anything else, because the file has a
    different name and a different set of contents. So a bug fixed in
    `shared_features.setup_logging` silently stays broken in ManagerBot, which
    is the bot whose whole job is noticing when something is broken.

    The four that legitimately differ are listed below with a reason each,
    the same way test_menu.py's UNADVERTISED and test_sql_safety.py's ALLOWED
    work: "it is in the exception list" is not an argument.

    python3 tests/test_shared_copies.py
"""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
BOTS = ["sticker_bot", "convert_bot", "downloader_bot", "anon_bot", "manager_bot"]
PUBLIC = [b for b in BOTS if b != "manager_bot"]

SHARED = ROOT / "shared"
# What lives in shared/ and nowhere else.
SHARED_MODULES = ("family_link.py", "live_message.py", "lifecycle.py", "problems.py", "shared_features.py",
                  "big_files.py", "envfiles.py", "pricing.py", "vault.py")

# monitoring.py functions that differ from shared_features.py on purpose.
DIFFERENT_ON_PURPOSE = {
    "attach_maintenance":
        "ManagerBot refuses to start without a job queue, so it has no "
        "degraded path -- and no per-user cache to prune, having one user.",
    "error_handler":
        "the public bots tell the person who hit a crash, with a code and a "
        "report button; ManagerBot's only user is the owner, who reads the "
        "traceback in the log and gets the crash alert anyway.",
    "flush_on_shutdown":
        "its db always has close_pool, so the getattr guard the four public "
        "bots need would be dead code here.",
    "_flush_activity_now":
        "ManagerBot has no /deletemydata (its one user is the owner), so its "
        "flush has no recently-erased people to leave out, and no erase to "
        "wait for.",
    "record_error":
        'says "ManagerBot itself hit ..." -- it reports on four other bots, so '
        "whose crash it is has to be in the sentence.",
    "track_activity":
        "does not stamp _last_seen, because nothing prunes user_data here; and it "
        "does not count button taps -- ManagerBot's buttons are the owner's.",
    "_sample_usage_now":
        "the public bots also write the window's button-tap counts with the usage "
        "row; ManagerBot counts none (its buttons are the owner's).",
    "_usage_sample_job":
        "the public bots swap the button-tap counts out on the event loop for "
        "_sample_usage_now to write; ManagerBot has none.",
}

failures = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL  ") + name + (f" -- {detail}" if not ok and detail else ""))
    if not ok:
        failures.append(name)


def functions(path: pathlib.Path) -> dict:
    """name -> normalised body. Docstrings are dropped: two copies that differ
    only in what they say about themselves are still the same code, and
    ManagerBot's comments legitimately talk about ManagerBot."""
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    out = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = node.body
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body = body[1:]
        out[node.name] = ast.dump(ast.Module(body=body, type_ignores=[]))
    return out


def main() -> int:
    print("=== the shared modules live once ===")
    missing = [name for name in SHARED_MODULES if not (SHARED / name).is_file()]
    check("every shared module is in shared/", not missing, ", ".join(missing))
    shared_names = {path.name for path in SHARED.glob("*.py")}
    shadowing = [f"{bot}/{path.name}" for bot in BOTS for path in (ROOT / bot).glob("*.py")
                 if path.name in shared_names]
    check("and no bot folder has one of its own, to win over it", not shadowing, ", ".join(shadowing))
    starts = [bot for bot in BOTS
              if 'parent.parent / "shared"' not in (ROOT / bot / "bot.py").read_text(encoding="utf-8")]
    check("every bot finds shared/ as it starts", not starts, ", ".join(starts))

    print("\n=== monitoring.py against shared_features.py ===")
    monitoring = functions(ROOT / "manager_bot" / "monitoring.py")
    shared = functions(SHARED / "shared_features.py")

    common = sorted(set(monitoring) & set(shared))
    check(f"monitoring.py still shares {len(common)} functions with shared_features.py",
          len(common) >= 15, f"only {len(common)}")

    drifted = []
    for name in common:
        if monitoring[name] == shared[name]:
            continue
        if name in DIFFERENT_ON_PURPOSE:
            print(f"  --  {name}: {DIFFERENT_ON_PURPOSE[name]}")
            continue
        drifted.append(name)
    check("nothing has drifted between the two", not drifted, ", ".join(drifted))

    # An exception that has stopped being one is an exception nobody rechecks.
    stale = [name for name in DIFFERENT_ON_PURPOSE
             if name not in common or monitoring.get(name) == shared.get(name)]
    check("every documented exception is still a real difference",
          not stale, ", ".join(stale))

    print("")
    print("FAILURES: " + str(len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
