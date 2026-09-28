"""Every child bot actually asks whether an update has been announced.

`/pause` from ManagerBot reaches each bot over the family bus, sets the
maintenance flag in its own process, and is then worth precisely as much as
the number of handlers that consult it. AnonBot's answer was zero: the flag
arrived, `lifecycle.is_paused()` would have said yes, and nothing asked --
so the owner announced an update, the bots said they were pausing, and
AnonBot carried on relaying messages. That is invisible in review, invisible
in the logs, and only shows up as a message somebody still received.

`shared_features.refuse_new_work()` is the one gate, so "does this bot call
it, from bot.py, at least once" is a question with a cheap and honest answer.
It does not check that every path is covered -- nothing here can -- but it
does stop a bot from having no gate at all, which is the failure that
actually happened.

ManagerBot is exempt: it is the bot doing the announcing and is never a
target of its own /pause (see _targets in manager_bot/bot.py).

    python tests/test_pause_gate.py
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CHILDREN = ["sticker_bot", "convert_bot", "downloader_bot", "anon_bot"]

failures = []
for bot in CHILDREN:
    path = os.path.join(REPO, bot, "bot.py")
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)

    imported = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "shared_features"
        and any(alias.name == "refuse_new_work" for alias in node.names)
        for node in ast.walk(tree)
    )
    calls = sum(
        1 for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "refuse_new_work"
    )

    if not imported:
        failures.append(f"{bot}/bot.py never imports refuse_new_work")
        print(f"  !!  {bot}: does not import refuse_new_work")
    elif calls == 0:
        failures.append(f"{bot}/bot.py imports refuse_new_work but never calls it")
        print(f"  !!  {bot}: imports refuse_new_work but never calls it")
    else:
        print(f"  ok  {bot}: asks before starting work ({calls} call site(s))")

sys.exit(1 if failures else 0)
