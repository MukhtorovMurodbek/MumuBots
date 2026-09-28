"""A job scheduled for "now" before the job queue starts must be told to run
however late it is.

APScheduler drops a job whose run time has already passed by more than
`misfire_grace_time` — which defaults to **one second**, and which
python-telegram-bot does not override. Every `run_once(..., when=0)` in this
family is scheduled from `main()`, before `run_polling()` starts the
scheduler, and between those two moments sits `post_init`: an advisory lock,
a table creation, the maintenance flag and `set_my_commands`, each a round
trip to a database on another continent. Several seconds, reliably.

So the job that starts the LISTEN listener was scheduled, silently discarded,
and never ran — on every bot, since the listener shipped in v1.0.1. Nothing
failed. `/status` said "NOT listening" with no error to show, because there
had been no error: there had been no attempt. The bus fell back to polling and
the only symptom was that everything took tens of seconds, which is the kind
of slow that gets attributed to the database being far away.

This is a source check rather than a runtime one because the failure is a
non-event, and there is nothing to observe at runtime except the absence of
something. Two things are asserted:

  * every `run_once(..., when=0)` passes `job_kwargs=RUN_LATE` (or an inline
    equivalent), and
  * `RUN_LATE` really is `{"misfire_grace_time": None}`.

`run_once` with a real delay is left alone: a job scheduled a minute out has
its own grace period against a queue that is already running, and the default
is the right one there.

    python tests/test_startup_jobs.py
"""
import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BOTS = ["sticker_bot", "convert_bot", "downloader_bot", "anon_bot", "manager_bot"]

failures = []

# --- RUN_LATE says what it should ------------------------------------------
fam = open(os.path.join(REPO, "shared", "family_link.py"), encoding="utf-8").read()
match = re.search(r'^RUN_LATE = (\{.*\})$', fam, re.M)
if not match:
    failures.append("family_link.RUN_LATE is not defined")
    print("  !!  family_link.py: RUN_LATE is not defined")
else:
    value = ast.literal_eval(match.group(1))
    if value != {"misfire_grace_time": None}:
        failures.append(f"RUN_LATE is {value!r}, not {{'misfire_grace_time': None}}")
        print(f"  !!  RUN_LATE is {value!r} -- a number here still drops the job")
    else:
        print("  ok  RUN_LATE = {'misfire_grace_time': None}")


def _immediate(call: ast.Call) -> bool:
    """run_once(..., when=0) -- the only shape at risk."""
    for kw in call.keywords:
        if kw.arg == "when":
            return isinstance(kw.value, ast.Constant) and kw.value.value == 0
    # positional: run_once(cb, 0)
    return (len(call.args) > 1 and isinstance(call.args[1], ast.Constant)
            and call.args[1].value == 0)


def _protected(call: ast.Call) -> bool:
    for kw in call.keywords:
        if kw.arg != "job_kwargs":
            continue
        node = kw.value
        if isinstance(node, ast.Name) and node.id == "RUN_LATE":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "RUN_LATE":
            return True
        if isinstance(node, ast.Dict):
            try:
                return ast.literal_eval(node) == {"misfire_grace_time": None}
            except ValueError:
                return False
    return False


checked = 0
for bot in BOTS:
    for name in ("bot.py", "family_link.py", "lifecycle.py"):
        # family_link and lifecycle live once, in shared/, since 2.0.0.
        path = os.path.join(REPO, bot if name == "bot.py" else "shared", name)
        if not os.path.exists(path):
            continue
        tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "run_once"):
                continue
            if not _immediate(node):
                continue
            checked += 1
            if not _protected(node):
                where = f"{bot}/{name}:{node.lineno}"
                failures.append(f"{where}: run_once(when=0) without job_kwargs=RUN_LATE")
                print(f"  !!  {where}: run_once(when=0) will be dropped if the "
                      f"queue starts more than a second later")

print(f"  ok  {checked} immediate run_once call(s) all run however late they are"
      if not failures else f"  ({checked} immediate run_once call(s) checked)")
sys.exit(1 if failures else 0)
