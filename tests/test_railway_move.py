"""scripts/railway_move.py, without Railway: what it would set, and whether it
knows when the five do not fit.

Its prepare step had never run against Railway until 2026-09-29, when a
project of its own showed two things the dry runs could not: the API refuses
`builder: "DOCKERFILE"` (400, "Problem processing request"), and the plan
this account is on allows 5 services in a project and 2 projects -- BotFamily
and five more is six. Checked here:

  - every setting it sends is one the API took that night;
  - DB_SCHEMA is set on every service (the base bots default to "public");
  - in BotFamily's project the secrets are references to BotFamily; in a
    project of its own there is no reference to BotFamily at all, and what
    is left to paste is listed;
  - it refuses, before creating anything, when the services would not fit,
    and when a new project would be one too many -- and says what to do.

    python3 tests/test_railway_move.py
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.argv = [sys.argv[0]]
import railway_move as rm  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL  ") + name + (f" -- {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


# The fields serviceInstanceUpdate accepted, one by one, on 2026-09-29.
ACCEPTED = {"dockerfilePath", "watchPatterns", "restartPolicyType", "restartPolicyMaxRetries", "numReplicas",
            "sleepApplication", "overlapSeconds", "drainingSeconds"}

for svc, folder, prefix, _bus, public in rm.BOTS:
    settings = rm.settings_for(folder)
    check(f"{svc}: only settings the API accepts", set(settings) <= ACCEPTED, str(set(settings) - ACCEPTED))
    check(f"{svc}: built from its own Dockerfile", settings["dockerfilePath"] == f"{folder}/Dockerfile")
    check(f"{svc}: one replica, never asleep", settings["numReplicas"] == 1 and settings["sleepApplication"] is False)
    for same in (True, False):
        where = "BotFamily's project" if same else "a project of its own"
        values = rm.variables_for(folder, prefix, public, same)
        check(f"{svc}, {where}: DB_SCHEMA is its folder", values.get("DB_SCHEMA") == folder)
        refs_old = any("${{" + rm.OLD + "." in v for v in values.values())
        check(f"{svc}, {where}: references to BotFamily only where BotFamily is", refs_old == same)
        if public:
            check(f"{svc}, {where}: policy links into MumuBots",
                  values["PRIVACY_URL"].endswith(f"{folder}/PRIVACY.md") and rm.REPO in values["TERMS_URL"])
            check(f"{svc}, {where}: its siblings listed", values["SIBLING_BOTS"].count(":") >= 8)
    paste = rm.paste_for(folder, prefix)
    check(f"{svc}: what to paste starts with its token, and includes the database",
          paste[0] == f"{prefix}_TOKEN" and "DATABASE_URL" in paste and "ADMIN_ID" in paste)
    check(f"{svc}: the API id only where files past 20 MB are moved",
          ("TELEGRAM_API_ID" in paste) == (folder in rm.BIG_FILE_BOTS))


def workspace(projects: int, per_project: int = 5, max_projects: int = 2):
    return {"id": "w", "subscriptionPlanLimit": {"projects": max_projects, "project": {"services": per_project}},
            "projects": {"edges": [{"node": {"name": f"p{i}"}} for i in range(projects)]}}


def room(target, ws, node, services):
    rm.TARGET = target
    return rm.room(ws, node, services)


old_only = {rm.OLD: {}}
said = room(rm.PROJECT_NAME, workspace(1), {"name": rm.PROJECT_NAME}, old_only)
check("BotFamily's project: 1 + 5 does not fit in 5, and it says so", bool(said) and "5" in said, str(said))
check("... and points at a project of their own", "--project MumuBots" in (said or ""), str(said))
check("a roomier plan fits", room(rm.PROJECT_NAME, workspace(1, per_project=50), {"name": "x"}, old_only) is None)
said = room("MumuBots", workspace(2), None, {})
check("a new project when there are already 2: refused", bool(said) and "2 projects" in said, str(said))
check("a new project with room for it: fine", room("MumuBots", workspace(1), None, {}) is None)
five = {svc: {} for svc, *_ in rm.BOTS}
check("the five already there: nothing to make, fits", room("MumuBots", workspace(2), {"name": "MumuBots"}, five) is None)

print()
if failures:
    print(f"FAILED ({len(failures)}):", *failures, sep="\n  ")
    sys.exit(1)
print("All railway_move checks passed.")
