#!/usr/bin/env python3
"""Move production between the two shapes on Railway: from BotFamily -- the
one-process C edition, one service -- to MumuBots' five services, one per
bot. And back, if it has to go back.

    python scripts/railway_move.py status               services, deployments, variable names, heartbeats
    python scripts/railway_move.py prepare [--apply]    the five services, their settings and variables;
                                                        no source, so nothing runs
    python scripts/railway_move.py pause [--apply]      pause the four public bots, as nightly_release.py does
    python scripts/railway_move.py stop-c [--apply]     BotFamily off: disconnected from its repository, its
                                                        running deployment removed -- the one process stops
    python scripts/railway_move.py connect [--apply]    the five connected to MumuBots@main, which deploys them
    python scripts/railway_move.py wait 2.0.0           until all five heartbeats report that version
    python scripts/railway_move.py resume [--apply]     resume the four public bots
    python scripts/railway_move.py rollback [--apply]   the five removed, BotFamily's last deployment back

Nothing happens without --apply: each command says what it would do first.

WHERE THE FIVE GO. By default into BotFamily's own project, taking their
tokens, usernames, admin ids and database by *reference* to the variables
BotFamily holds -- `${{BotFamily.SBOT_TOKEN}}` -- which Railway resolves on
its side, so no secret passes through here. But the plan this account is on
(Railway's API, 2026-09-29) allows 5 services in a project and 2 projects:
BotFamily and five more is six, and prepare refuses before creating any.
So there is a second way:

    --project MumuBots    the five in a project of their own, made if it is
                          missing; BotFamily stays where it is, for the
                          rollback. References do not cross projects, so the
                          secrets are pasted into each service by hand --
                          prepare lists which -- and SIBLING_BOTS refers to
                          the usernames pasted on the four public services.

Every command takes the same --project. The status command prints variable
names, never values.

It uses the Railway CLI's own login (`railway login` once), refreshed by
running `railway whoami`, and ManagerBot's settings for the family database.

THE ORDER (docs/changes/CHANGES_v2.0.0.md, "Production moves to MumuBots"):
prepare any time before, and paste; at 03:40 Tashkent pause; at 04:00, once
nobody has been active for 15 minutes, stop-c; connect; wait; resume. The
poll lease makes the handover safe: each new bot waits for the old process
to let go of its token, and nothing is lost while it waits -- Telegram keeps
a bot's updates for a day.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://backboard.railway.com/graphql/v2"
PROJECT_NAME = "spectacular-ambition"   # where BotFamily runs
OLD = "BotFamily"
REPO = "MukhtorovMurodbek/MumuBots"
BRANCH = "main"
BOTS = [  # service, folder, prefix, bus id, public
    ("StickerBot", "sticker_bot", "SBOT", "stickerbot", True),
    ("ConvertBot", "convert_bot", "CBOT", "convertbot", True),
    ("DownloaderBot", "downloader_bot", "DBOT", "downloaderbot", True),
    ("AnonBot", "anon_bot", "ABOT", "anonbot", True),
    ("ManagerBot", "manager_bot", "MBOT", "managerbot", False),
]
# The two that move files past the Bot API's limits, given the app's API id.
BIG_FILE_BOTS = ("convert_bot", "downloader_bot")


def _option(name: str) -> str | None:
    argv = sys.argv[1:]
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else None


APPLY = "--apply" in sys.argv
TARGET = _option("--project") or PROJECT_NAME   # where the five go


# ---- Railway ---------------------------------------------------------------

def _token() -> str:
    subprocess.run("railway whoami", capture_output=True, timeout=60, shell=True)   # refreshes the login
    user = json.loads((Path.home() / ".railway" / "config.json").read_text())["user"]
    return user.get("accessToken") or user.get("token")


def gql(query: str, variables: dict | None = None) -> dict:
    request = urllib.request.Request(API, data=json.dumps({"query": query, "variables": variables or {}}).encode(),
                                     headers={"Authorization": f"Bearer {_token()}",
                                              "Content-Type": "application/json", "User-Agent": "railway-move"})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = json.loads(response.read())
    if data.get("errors"):
        raise SystemExit("Railway said: " + json.dumps(data["errors"])[:600])
    return data["data"]


PROJECTS = """query { me { workspaces { id subscriptionPlanLimit projects { edges { node { id name
  environments { edges { node { id name } } }
  services { edges { node { id name serviceInstances { edges { node { environmentId source { repo image }
    latestDeployment { id status createdAt } } } } } } } } } } } } }"""


def find(name: str):
    """(workspace, project, its production environment, {service name: service}),
    with None and {} for a project that is not there."""
    workspaces = gql(PROJECTS)["me"]["workspaces"]
    for workspace in workspaces:
        for edge in workspace["projects"]["edges"]:
            node = edge["node"]
            if node["name"] == name:
                env = next(e["node"] for e in node["environments"]["edges"] if e["node"]["name"] == "production")
                return workspace, node, env, {s["node"]["name"]: s["node"] for s in node["services"]["edges"]}
    return workspaces[0], None, None, {}


def project(name: str = PROJECT_NAME):
    _workspace, node, env, services = find(name)
    if node is None:
        raise SystemExit(f"No project {name!r} on this Railway login.")
    return node, env, services


def limits(workspace: dict) -> tuple[int | None, int | None]:
    """(services a project may hold, projects the account may hold), from the plan."""
    plan = workspace.get("subscriptionPlanLimit") or {}
    return (plan.get("project") or {}).get("services"), plan.get("projects")


def instance(service: dict, env: dict) -> dict:
    return next((i["node"] for i in service["serviceInstances"]["edges"] if i["node"]["environmentId"] == env["id"]), {})


def act(what: str, do):
    print(("DO    " if APPLY else "WOULD ") + what)
    if APPLY:
        result = do()
        if result is not None:
            print("      -> " + json.dumps(result)[:300])
        return result
    return None


def ref(name: str) -> str:
    return "${{" + OLD + "." + name + "}}"


def variables_for(folder: str, prefix: str, public: bool, same_project: bool = True) -> dict:
    if same_project:
        values = {
            f"{prefix}_TOKEN": ref(f"{prefix}_TOKEN"),
            f"{prefix}_USERNAME": ref(f"{prefix}_USERNAME"),
            f"{prefix}_ADMIN_ID": ref("ADMIN_ID"),
            "DATABASE_URL": ref("DATABASE_URL"),
            "SIBLING_BOTS": ",".join(f"{bus}:{svc}:" + ref(f"{p}_USERNAME") for svc, _f, p, bus, pub in BOTS if pub),
        }
    else:
        # The secrets are pasted (paste_for); the usernames the siblings show
        # are read from the services they are pasted on.
        values = {"SIBLING_BOTS": ",".join(f"{bus}:{svc}:" + "${{" + f"{svc}.{p}_USERNAME" + "}}"
                                           for svc, _f, p, bus, pub in BOTS if pub)}
    # The base bots default to "public" -- never where the family's tables are.
    values["DB_SCHEMA"] = folder
    if public:
        values["PRIVACY_URL"] = f"https://github.com/{REPO}/blob/{BRANCH}/{folder}/PRIVACY.md"
        values["TERMS_URL"] = f"https://github.com/{REPO}/blob/{BRANCH}/{folder}/TERMS.md"
    return values


def paste_for(folder: str, prefix: str) -> list[str]:
    """What the owner pastes into a service of a project of its own."""
    names = [f"{prefix}_TOKEN", f"{prefix}_USERNAME", "ADMIN_ID", "DATABASE_URL"]
    if folder in BIG_FILE_BOTS:
        names += ["TELEGRAM_API_ID", "TELEGRAM_API_HASH"]
    return names


def settings_for(folder: str) -> dict:
    # What <folder>/railway.json says, set on the service itself: Railway
    # stops reading config files on 2026-12-01 (its CLI said so, 2026-09-28).
    # No "builder": the API refuses DOCKERFILE (400, checked 2026-09-29), and
    # a Dockerfile path is what makes the build a Dockerfile build.
    return {"dockerfilePath": f"{folder}/Dockerfile",
            "watchPatterns": [f"/{folder}/**", "/shared/**"],
            "restartPolicyType": "ON_FAILURE", "restartPolicyMaxRetries": 10, "numReplicas": 1,
            "sleepApplication": False, "overlapSeconds": 30, "drainingSeconds": 120}


# ---- the family database -------------------------------------------------------

def _release():
    sys.path.insert(0, str(ROOT / "scripts"))
    import nightly_release
    admins, _target = nightly_release._setup()
    import db  # ManagerBot's, as nightly_release uses it
    return nightly_release, db, admins


def heartbeats() -> dict:
    _nr, db, _admins = _release()
    rows = db.all_heartbeats()
    print("  heartbeats:")
    for row in sorted(rows, key=lambda r: r["bot_id"]):
        print(f"    {row['bot_id']:<22} {row['version']:<8} {str(row.get('host'))[:20]:<20} {row.get('last_seen')}")
    return {row["bot_id"]: row for row in rows}


# ---- the steps -------------------------------------------------------------------

def _show(node, env, services):
    print(f"project {node['name']}, environment {env['name']}")
    for name, service in services.items():
        inst = instance(service, env)
        deployment = inst.get("latestDeployment") or {}
        names = gql("query($p:String!,$e:String!,$s:String){ variables(projectId:$p, environmentId:$e, serviceId:$s) }",
                    {"p": node["id"], "e": env["id"], "s": service["id"]})["variables"]
        shown = ", ".join(f"{k}({'ref' if '${{' in (v or '') else 'set'})" for k, v in sorted(names.items())
                          if not k.startswith("RAILWAY_"))
        print(f"  {name:<14} source={inst.get('source')} latest={deployment.get('status')} {deployment.get('createdAt')}")
        print(f"                 variables: {shown}")


def status():
    _show(*project(PROJECT_NAME))
    if TARGET != PROJECT_NAME:
        _workspace, node, env, services = find(TARGET)
        if node is None:
            print(f"project {TARGET}: not made yet (prepare makes it)")
        else:
            _show(node, env, services)
    heartbeats()


def room(workspace: dict, node, services: dict) -> str | None:
    """Why the five will not fit where they are going, or None."""
    per_project, projects = limits(workspace)
    missing = [svc for svc, *_rest in BOTS if svc not in services]
    if node is None and projects is not None and len(workspace["projects"]["edges"]) >= projects:
        return (f"the plan allows {projects} projects and there are already "
                f"{len(workspace['projects']['edges'])}; delete one first")
    if per_project is not None and len(services) + len(missing) > per_project:
        advice = " -- give them a project of their own: --project MumuBots" if TARGET == PROJECT_NAME else ""
        return (f"{TARGET} holds {len(services)} services and the plan allows {per_project} a project, "
                f"so the {len(missing)} still to make do not fit{advice}")
    return None


def prepare():
    same = TARGET == PROJECT_NAME
    workspace, node, env, services = find(TARGET)
    if node is None and same:
        raise SystemExit(f"No project {TARGET!r} on this Railway login.")
    problem = room(workspace, node, services)
    if problem:
        raise SystemExit(f"Not preparing: {problem}.")
    if node is None:
        made = act(f"create the project {TARGET}",
                   lambda: gql("mutation($i:ProjectCreateInput!){ projectCreate(input:$i){ id name "
                               "environments { edges { node { id name } } } } }",
                               {"i": {"name": TARGET, "workspaceId": workspace["id"],
                                      "description": "MumuBots: the five bots, one service each."}}))
        if not APPLY:
            for svc, folder, prefix, _bus, public in BOTS:
                print(f"WOULD create {svc} there, with no source, settings {settings_for(folder)}")
                print(f"      variables {', '.join(sorted(variables_for(folder, prefix, public, False)))}; "
                      f"to paste: {', '.join(paste_for(folder, prefix))}")
            return
        node = made["projectCreate"]
        env = next(e["node"] for e in node["environments"]["edges"] if e["node"]["name"] == "production")
    for svc, folder, prefix, _bus, public in BOTS:
        service = services.get(svc)
        if service is None:
            made = act(f"create {svc}, with no source: nothing is built or run",
                       lambda: gql("mutation($i:ServiceCreateInput!){ serviceCreate(input:$i){ id name } }",
                                   {"i": {"projectId": node["id"], "name": svc, "environmentId": env["id"]}}))
            if not APPLY:
                continue
            service_id = made["serviceCreate"]["id"]
        else:
            service_id = service["id"]
            print(f"      {svc} is there")
        act(f"{svc}: build and deploy settings {settings_for(folder)}",
            lambda: gql("mutation($s:String!,$e:String!,$i:ServiceInstanceUpdateInput!){ "
                        "serviceInstanceUpdate(serviceId:$s, environmentId:$e, input:$i) }",
                        {"s": service_id, "e": env["id"], "i": settings_for(folder)}))
        values = variables_for(folder, prefix, public, same)
        how = f"the secret ones by reference to {OLD}" if same else "none of them secret"
        act(f"{svc}: variables {', '.join(sorted(values))} -- {how}, no redeploy",
            lambda: gql("mutation($i:VariableCollectionUpsertInput!){ variableCollectionUpsert(input:$i) }",
                        {"i": {"projectId": node["id"], "environmentId": env["id"], "serviceId": service_id,
                               "variables": values, "skipDeploys": True}}))
        if not same:
            print(f"      paste into {svc}: {', '.join(paste_for(folder, prefix))}")


def queue(command: str, args: str = ""):
    nightly_release, db, admins = _release()
    return act(f"queue `{command} {args}` for the four public bots",
               lambda: nightly_release.queue(db, admins, command, args))


def stop_c():
    node, env, services = project(PROJECT_NAME)
    service = services[OLD]
    inst = instance(service, env)
    act(f"disconnect {OLD} from {inst.get('source')}: a push there will not deploy it any more",
        lambda: gql("mutation($id:String!){ serviceDisconnect(id:$id){ id } }", {"id": service["id"]}))
    deployment = inst.get("latestDeployment") or {}
    if deployment.get("id") and deployment.get("status") not in ("REMOVED", "REMOVING"):
        act(f"remove {OLD}'s deployment {deployment['id']} ({deployment.get('status')}): the one process "
            f"stops and lets go of every bot's token",
            lambda: gql("mutation($id:String!){ deploymentRemove(id:$id) }", {"id": deployment["id"]}))


def connect():
    _node, _env, services = project(TARGET)
    for svc, *_rest in BOTS:
        service = services.get(svc)
        if service is None:
            print(f"      {svc} is missing -- prepare first")
            continue
        act(f"connect {svc} to {REPO}@{BRANCH}, which builds and deploys it",
            lambda: gql("mutation($id:String!,$i:ServiceConnectInput!){ serviceConnect(id:$id, input:$i){ id } }",
                        {"id": service["id"], "i": {"repo": REPO, "branch": BRANCH}}))


def wait(version: str, minutes: int = 25) -> bool:
    deadline = time.monotonic() + minutes * 60
    while True:
        beats = heartbeats()
        ready = [bus for _s, _f, _p, bus, _pub in BOTS if (beats.get(bus) or {}).get("version") == version]
        if len(ready) == len(BOTS):
            print(f"all five report {version}")
            return True
        if time.monotonic() > deadline:
            print(f"gave up waiting; on {version}: {ready}")
            return False
        time.sleep(30)


def rollback():
    node, env, services = project(TARGET)
    for svc, *_rest in BOTS:
        service = services.get(svc)
        deployment = (instance(service, env).get("latestDeployment") or {}) if service else {}
        if deployment.get("id"):
            act(f"remove {svc}'s deployment {deployment['id']}",
                lambda: gql("mutation($id:String!){ deploymentRemove(id:$id) }", {"id": deployment["id"]}))
    _node, old_env, old_services = project(PROJECT_NAME)
    edges = gql("""query($s:String!,$e:String!){ deployments(first: 10, input:{serviceId:$s, environmentId:$e}) {
                   edges { node { id status createdAt } } } }""",
                {"s": old_services[OLD]["id"], "e": old_env["id"]})["deployments"]["edges"]
    last = next((e["node"] for e in edges if e["node"]["status"] in ("SUCCESS", "REMOVED")), None)
    if last:
        act(f"redeploy {OLD}'s deployment of {last['createdAt']} as it was",
            lambda: gql("mutation($id:String!){ deploymentRedeploy(id:$id, usePreviousImageTag:true){ id } }",
                        {"id": last["id"]}))


def main() -> int:
    words, skip = [], False
    for arg in sys.argv[1:]:
        if skip:
            skip = False
        elif arg == "--project":
            skip = True
        elif not arg.startswith("--"):
            words.append(arg)
    command, rest = (words[0] if words else "status"), words[1:]
    if command == "status":
        status()
    elif command == "prepare":
        prepare()
    elif command == "pause":
        queue("pause", rest[0] if rest else "80")
    elif command == "resume":
        queue("resume")
    elif command == "stop-c":
        stop_c()
    elif command == "connect":
        connect()
    elif command == "wait":
        return 0 if wait(rest[0] if rest else "2.0.0") else 1
    elif command == "rollback":
        rollback()
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
