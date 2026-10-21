"""The Telegram command menu against the commands each bot actually handles.

`set_my_commands` fills the menu a user sees when they tap the slash. It is a
hand-written list beside a hand-written set of handlers, which is exactly the
pair that drifts: a command gets added and never advertised, or renamed and
left in the menu pointing at nothing. Both are invisible from the code and
obvious to a user.

Since v1.4.0 there are two lists per bot, because a menu has a scope. The four
public bots publish BOT_COMMANDS to everybody and BOT_COMMANDS + ADMIN_COMMANDS
into each admin's own chat; ManagerBot, being private, inverts it and publishes
its one refusal to everybody. So the drift this catches now runs both ways: a
public command missing from the public menu, and an owner-only command missing
from the owner's.

Read with ast rather than by importing, so this needs nothing stubbed -- and
an owner-only handler is recognised by what it does (it checks _is_admin)
rather than by what it is called.
"""
import ast, pathlib, re, sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
BOTS = ["sticker_bot", "convert_bot", "downloader_bot", "anon_bot"]

# Handled on purpose, and advertised on purpose *not*. Anything listed here
# needs a reason, because "it is in the exception list" is how a menu quietly
# stops describing the bot.
UNADVERTISED = {
    ("sticker_bot", "convert"):
        "a redirect to @ConvertBot, kept for people still typing it out of "
        "habit -- offering it in the menu would advertise a command whose "
        "whole answer is 'use a different bot'",
    ("convert_bot", "donate"):
        "the old name of /recharge, kept working for anyone who learned it -- "
        "ConvertBot sells credit, so its menu calls it recharging, and listing "
        "both would offer the same thing twice",
}

# The language switches, in every bot. Each is written in the language it
# switches TO, which is what makes them the way back for somebody who cannot
# read the menu at all -- so they must never stop working. What they do not
# need is three lines in every menu for what /language already does with one,
# and /language is second from the top in all four.
for _bot in BOTS:
    for _switch in ("en", "uz", "rus"):
        UNADVERTISED[(_bot, _switch)] = (
            "a one-word language switch, written in the language it switches "
            "to; /language covers all three and sits second in the menu")

# 2.2.0: the full guide in one tap, in every public bot. The per-command pages
# are the buttons under /help (and /help <name>), not commands: check_help_pages
# below keeps any help_<name> command out of every bot.
for _bot in BOTS:
    UNADVERTISED[(_bot, "fullhelp")] = (
        "the full guide in one tap -- the same as /help full and the button "
        "under /help; the menu has /help, which leads to it")

# Commands that became a button, a screen, or another command's job in 1.7.0.
# Every one of them still works when typed: somebody learned it, and a command
# that answers is cheaper to keep than a habit is to break.
UNADVERTISED.update({
    ("convert_bot", "mystars"):
        "has called /balance since credit replaced Stars -- one command "
        "showing the whole history beats two showing halves of it",
    ("downloader_bot", "caption"):
        "one of the two switches on /settings, which shows both with their "
        "state on them",
    ("downloader_bot", "lossless"):
        "the other switch on /settings, and a word that only means something "
        "to somebody who already knows it",
    ("anon_bot", "newlink"):
        "the New link button on the /link screen",
    ("anon_bot", "pause"):
        "the Pause button on the /link screen -- and the same word means "
        "'stop the bots for an update' in ManagerBot, which it is not",
    ("anon_bot", "resume"):
        "the Resume button on the /link screen, same as /pause",
    ("anon_bot", "stats"):
        "its two counts are on the /link screen, which is the page about the "
        "inbox they count",
    ("anon_bot", "archive"):
        "files the conversation you are replying to; /conversations has an "
        "Archive button on every row",
})

failures = []
def check(name, ok):
    print(("  ok  " if ok else "  FAIL  ") + name)
    if not ok:
        failures.append(name)


def owner_only_denials(bot):
    """Every `if not _is_admin(...)` guard, and whether its body answers.

    A guard that just returns is not silence to the person who typed the
    command -- it is a bot that replies to everything except one thing,
    which is a fairly loud way to point at that thing. It also looks broken
    to anyone who typed it by mistake. Answering with the same sentence a
    typo gets is both quieter and more honest.
    """
    tree = ast.parse((REPO / bot / "bot.py").read_text(encoding="utf-8"))
    guards = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not (isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not)):
            continue
        call = test.operand
        if not isinstance(call, ast.Call):
            continue
        name = (call.func.id if isinstance(call.func, ast.Name)
                else getattr(call.func, "attr", None))
        if name != "_is_admin":
            continue
        answers = any(
            isinstance(sub, ast.Call)
            and (sub.func.id if isinstance(sub.func, ast.Name)
                 else getattr(sub.func, "attr", None)) in ("_deny", "unknown_command")
            for stmt in node.body for sub in ast.walk(stmt)
        )
        guards.append((node.lineno, answers))
    return guards


def command_lists(tree):
    """Every module-level `X_COMMANDS = [BotCommand(...), ...]`, by name.

    Read by assignment rather than by walking the file for BotCommand calls,
    because there are two lists now and which one a command is in is the whole
    point of the check.
    """
    out = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.List):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name) or not target.id.endswith("COMMANDS"):
                continue
            names = []
            for element in node.value.elts:
                if (isinstance(element, ast.Call)
                        and getattr(element.func, "id", None) == "BotCommand"
                        and element.args
                        and isinstance(element.args[0], ast.Constant)):
                    names.append(element.args[0].value)
            out[target.id] = names
    return out


def analyse(bot):
    tree = ast.parse((REPO / bot / "bot.py").read_text(encoding="utf-8"))
    admin_fns, handlers = set(), {}

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for sub in ast.walk(node):
                named = (sub.id if isinstance(sub, ast.Name)
                         else sub.attr if isinstance(sub, ast.Attribute) else None)
                if named in ("_is_admin", "is_admin"):
                    admin_fns.add(node.name)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = (node.func.id if isinstance(node.func, ast.Name)
                else getattr(node.func, "attr", None))
        if name == "CommandHandler" and node.args:
            target = node.args[0]
            if isinstance(target, ast.Constant) and isinstance(target.value, str):
                names = [target.value]
            elif isinstance(target, (ast.List, ast.Tuple)):
                names = [e.value for e in target.elts
                         if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            else:
                names = []
            callback = node.args[1] if len(node.args) > 1 else None
            cb = getattr(callback, "id", getattr(callback, "attr", "?"))
            for n in names:
                handlers[n] = cb
    return handlers, command_lists(tree), admin_fns


# Written in the language they switch TO, and left out of every translation on
# purpose -- they are the way back for somebody who chose the wrong language,
# so they have to be legible whatever the menu is currently in.
SELF_DESCRIBING = {"language", "en", "uz", "rus"}

# Telegram's own limits on a BotCommand.
MAX_DESCRIPTION = 256
MAX_NAME = 32

# What a person can actually read. The menu is a popup and a long line is cut
# where the window ends: the owner's screenshot of ManagerBot's /problems (54
# characters) read "...the midnight report" and stopped, in a window about as
# wide as a phone's. 50 is where that line was cut; every other description in
# the family was already under it.
MAX_VISIBLE = 50
# Three languages in one line is what /language is for -- it is the way out for
# somebody who cannot read the menu -- and it is 51 characters.
LONG_ON_PURPOSE = {"language"}


def descriptions(bot):
    """Every `X_COMMANDS = [BotCommand(name, description), ...]` in bot.py."""
    tree = ast.parse((REPO / bot / "bot.py").read_text(encoding="utf-8"))
    out = []
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.List):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id.endswith("COMMANDS"):
                for element in node.value.elts:
                    if (isinstance(element, ast.Call) and getattr(element.func, "id", None) == "BotCommand"
                            and len(element.args) >= 2
                            and all(isinstance(a, ast.Constant) for a in element.args[:2])):
                        out.append((target.id, element.args[0].value, element.args[1].value))
    return out


def check_visible(bot):
    """No description is longer than a popup shows, in any language."""
    rows = [("en", name, text) for _, name, text in descriptions(bot)]
    for language, table in command_menu(bot).items() if (REPO / bot / "i18n.py").exists() else []:
        rows += [(language, name, text) for name, text in table.items()]
    cut = [f"/{name} ({language}, {len(text)})" for language, name, text in rows
           if len(text) > MAX_VISIBLE and name not in LONG_ON_PURPOSE]
    check(f"{bot}: every menu description fits the popup (under {MAX_VISIBLE + 1} characters)"
          + (f" -- {', '.join(cut)}" if cut else ""), not cut)


def command_menu(bot):
    """`COMMAND_MENU` out of the bot's i18n.py, without importing it."""
    tree = ast.parse((REPO / bot / "i18n.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "COMMAND_MENU":
                return ast.literal_eval(node.value)
    return {}


def check_translations(bot, menu, admin_menu):
    """The menu in the other two languages, against the English one.

    English lives in bot.py and these live in i18n.py, which is the same shape
    of split as every other string in the family -- and the same way it goes
    wrong. A command added to BOT_COMMANDS and not here still appears for a
    Russian user, in English, which is the failure this catches while it is
    still one line rather than a screenshot.
    """
    table = command_menu(bot)
    check(f"{bot}: the menu is translated at all", bool(table))

    for language in ("uz", "ru"):
        entries = table.get(language) or {}
        check(f"{bot}: {language} is there", bool(entries))

        missing = [c for c in menu if c not in SELF_DESCRIBING and c not in entries]
        check(f"{bot}: every {language} description is written"
              + (f" -- {', '.join(missing)}" if missing else ""), not missing)

        stale = [c for c in entries if c not in menu]
        check(f"{bot}: no {language} description names a command that is gone"
              + (f" -- {', '.join(stale)}" if stale else ""), not stale)

        # The four are not an oversight, so a translation appearing for one of
        # them is somebody having disagreed with the rule without saying so.
        translated = [c for c in entries if c in SELF_DESCRIBING]
        check(f"{bot}: {language} leaves the language switches alone"
              + (f" -- {', '.join(translated)}" if translated else ""), not translated)

        # The owner's half stays English by policy, along with everything else
        # this family prints for an admin.
        leaked = [c for c in entries if c in admin_menu]
        check(f"{bot}: {language} does not translate the owner-only commands"
              + (f" -- {', '.join(leaked)}" if leaked else ""), not leaked)

        too_long = [c for c, text in entries.items() if len(text) > MAX_DESCRIPTION]
        check(f"{bot}: no {language} description is over {MAX_DESCRIPTION} characters"
              + (f" -- {', '.join(too_long)}" if too_long else ""), not too_long)

        blank = [c for c, text in entries.items() if not text.strip()]
        check(f"{bot}: no {language} description is empty"
              + (f" -- {', '.join(blank)}" if blank else ""), not blank)


def check_public(bot):
    handlers, lists, admin_fns = analyse(bot)
    menu = lists.get("BOT_COMMANDS", [])
    owner_menu = lists.get("ADMIN_COMMANDS", [])
    admin = {c for c, fn in handlers.items() if fn in admin_fns}
    public = {c for c in handlers if c not in admin}

    check(f"{bot}: the menu is not empty", bool(menu))
    check(f"{bot}: no command is listed twice", len(menu) == len(set(menu)))

    stale = [c for c in menu + owner_menu if c not in handlers]
    check(f"{bot}: every advertised command has a handler"
          + (f" -- {', '.join(stale)}" if stale else ""), not stale)

    leaked = [c for c in menu if c in admin]
    check(f"{bot}: no owner-only command is in the public menu"
          + (f" -- {', '.join(leaked)}" if leaked else ""), not leaked)

    missing = [c for c in sorted(public)
               if c not in menu and (bot, c) not in UNADVERTISED]
    check(f"{bot}: every public command is advertised"
          + (f" -- {', '.join(missing)}" if missing else ""), not missing)

    # The other direction, and the one that had never been checked. An
    # owner-only command kept out of everybody's menu *including the owner's*
    # is a command they have to remember unaided, which over a few versions
    # means a command they stop using. BotCommandScopeChat publishes these
    # into the owner's chat only; see shared_features.publish_commands().
    unlisted = [c for c in sorted(admin) if c not in owner_menu]
    check(f"{bot}: every owner-only command is in the owner's menu"
          + (f" -- {', '.join(unlisted)}" if unlisted else ""), not unlisted)

    doubled = [c for c in owner_menu if c in menu]
    check(f"{bot}: nothing is listed in both menus"
          + (f" -- {', '.join(doubled)}" if doubled else ""), not doubled)

    # The owner's menu is the public one plus these, so anything in here that
    # is not actually gated would be a public command advertised twice.
    strays = [c for c in owner_menu if c not in admin]
    check(f"{bot}: the owner's menu holds only owner-only commands"
          + (f" -- {', '.join(strays)}" if strays else ""), not strays)

    # An exception that stops being true is worse than no exception: it
    # hides a command that has since become worth offering.
    for (b, cmd), why in UNADVERTISED.items():
        if b != bot:
            continue
        check(f"{bot}: /{cmd} is still handled ({why[:40]}...)", cmd in handlers)
        check(f"{bot}: /{cmd} is still kept out of both menus",
              cmd not in menu and cmd not in owner_menu)

    # Owner-only commands are registered handlers, so they match and
    # never reach the unknown-command reply on their own.
    guards = owner_only_denials(bot)
    check(f"{bot}: it has owner-only commands to protect", bool(guards))
    silent = [line for line, answers in guards if not answers]
    check(f"{bot}: every owner-only command answers a stranger"
          + (f" -- silent at line(s) {silent}" if silent else ""), not silent)

    # /start is the one every bot has and the one most easily forgotten,
    # because Telegram shows a Start button and the menu entry looks
    # redundant -- until someone wants the instructions back.
    check(f"{bot}: /start is offered", "start" in menu)

    # What Telegram will actually accept, for both lists at once: a name is
    # 1-32 characters of lowercase letters, digits and underscores, and a
    # description is 1-256. A rejected list is a set_my_commands that fails at
    # startup and a menu that silently stays on the previous version.
    bad = [c for c in menu + owner_menu
           if not c or len(c) > MAX_NAME or not re.fullmatch(r"[a-z0-9_]+", c)]
    check(f"{bot}: every command name is one Telegram accepts"
          + (f" -- {', '.join(bad)}" if bad else ""), not bad)

    check_translations(bot, menu, set(owner_menu))
    check_help(bot, menu)
    check_help_pages(bot, handlers, lists)


# Every command a user can send, named in /help, in every language: the
# menu's, and the ones kept out of the menu on purpose (UNADVERTISED above) --
# the menu is a list of names, /help is the one place each is explained, and
# a command that still works is one somebody will type.
#
# Found missing in 2.0.0: AnonBot's /conversations and /which, in its menu
# since 1.7.0 while its /help still listed what had moved onto the /link
# screen; and the account and policy commands (/balance, /paysupport,
# /privacy, /terms, /deletemydata, /start), which no bot's /help had ever
# listed -- the owner: "/help should list all user commands."
HELP_NEED_NOT_NAME = {"help"}


def help_texts(bot):
    """Every "help_text" in the bot's i18n.py, one per language, read with ast."""
    tree = ast.parse((REPO / bot / "i18n.py").read_text(encoding="utf-8"))
    texts = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "help_text":
                    texts.append(ast.literal_eval(value))
    return texts


def check_help(bot, menu):
    texts = help_texts(bot)
    check(f"{bot}: /help is written in every language", len(texts) >= 3)
    commands = list(menu) + sorted(cmd for (b, cmd) in UNADVERTISED if b == bot and cmd not in menu)
    for number, text in enumerate(texts, 1):
        named = set(re.findall(r"(?<![\w<])/([a-z][a-z0-9_]+)", text))
        unexplained = [c for c in commands if c not in named and c not in HELP_NEED_NOT_NAME]
        check(f"{bot}: /help #{number} names every command a user can send"
              + (f" -- not {', '.join('/' + c for c in unexplained)}" if unexplained else ""),
              not unexplained)


# The per-command help pages (2.2.0, the owner 2026-10-17): a button under /help
# (and /help <name>) shows a page, in a bot whose bot.py holds HELP_PAGES
# (name -> function) and HELP_ALIASES (another name -> a page). A page is named
# after the command it explains, so that the button "/convert" is the page of
# /convert and nothing has to be looked up; the one page that is not a command's
# is listed here with its reason.
TOPIC_PAGES = {
    ("downloader_bot", "download"):
        "sending a link is not a command -- there is nothing to type but the "
        "link -- and it is the part of this bot people most need explained",
}
# The pattern of shared_features.HELP_PAGE_PATTERN, for the name inside "helpdet:<name>".
PAGE_NAME = re.compile(r"[a-z]{1,20}")


def module_dict(bot, name):
    """A module-level `NAME = {"key": value, ...}` in bot.py, as {key: value}: a string
    value as itself, anything else as its source. None when it is not a dict literal
    with string keys."""
    tree = ast.parse((REPO / bot / "bot.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            if not isinstance(node.value, ast.Dict):
                return None
            if not all(isinstance(k, ast.Constant) and isinstance(k.value, str) for k in node.value.keys):
                return None
            return {k.value: (v.value if isinstance(v, ast.Constant) else ast.unparse(v))
                    for k, v in zip(node.value.keys, node.value.values)}
    return None


def registers_page_button(bot):
    """Is there a `CallbackQueryHandler(help_page_button(HELP_PAGES), pattern=HELP_PAGE_PATTERN)`?
    Read as a shape, not as text, so that another pages dict or another pattern shows up."""
    tree = ast.parse((REPO / bot / "bot.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "CallbackQueryHandler" and node.args):
            continue
        handler = node.args[0]
        if not (isinstance(handler, ast.Call) and getattr(handler.func, "id", None) == "help_page_button"):
            continue
        pattern = [kw.value for kw in node.keywords if kw.arg == "pattern"]
        return (len(handler.args) == 1 and getattr(handler.args[0], "id", None) == "HELP_PAGES"
                and len(pattern) == 1 and getattr(pattern[0], "id", None) == "HELP_PAGE_PATTERN")
    return False


def help_commands_registered(bot):
    """The `help_<x>` commands a bot registers: a CommandHandler whose name, or one of whose
    names, is written as help_... (a string, a list of them, or an f-string)."""
    tree = ast.parse((REPO / bot / "bot.py").read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "CommandHandler" and node.args):
            continue
        target = node.args[0]
        parts = target.elts if isinstance(target, (ast.List, ast.Tuple)) else [target]
        for part in parts:
            if isinstance(part, ast.Constant) and isinstance(part.value, str) and part.value.startswith("help_"):
                found.append(part.value)
            elif isinstance(part, ast.JoinedStr) and part.values and isinstance(part.values[0], ast.Constant) \
                    and str(part.values[0].value).startswith("help_"):
                found.append("help_{...}")
    return found


def check_help_pages(bot, handlers, lists):
    pages, aliases = module_dict(bot, "HELP_PAGES"), module_dict(bot, "HELP_ALIASES")
    check(f"{bot}: HELP_PAGES and HELP_ALIASES are dict literals with string keys, and there is a page",
          pages is not None and aliases is not None and bool(pages))
    pages, aliases = pages or {}, aliases or {}

    bad = [name for name in pages if not PAGE_NAME.fullmatch(name)]
    check(f"{bot}: every page name fits the button's callback data (helpdet:<a-z, 1 to 20 letters>)"
          + (f" -- {', '.join(bad)}" if bad else ""), not bad)

    # A page is the page of a command the bot handles, or a topic on purpose.
    strays = [name for name in pages if name not in handlers and (bot, name) not in TOPIC_PAGES]
    check(f"{bot}: every page is a command the bot handles, or a listed topic"
          + (f" -- {', '.join(strays)}" if strays else ""), not strays)
    for (b, name), why in TOPIC_PAGES.items():
        if b == bot:
            check(f"{bot}: the topic page '{name}' is still a page, and still not a command ({why[:36]}...)",
                  name in pages and name not in handlers)

    lost = [alias for alias, page in aliases.items() if page not in pages]
    check(f"{bot}: every alias stands for a page"
          + (f" -- {', '.join(lost)}" if lost else ""), not lost)
    doubled = [alias for alias in aliases if alias in pages]
    check(f"{bot}: no alias is also a page" + (f" -- {', '.join(doubled)}" if doubled else ""), not doubled)

    # The pages and /fullhelp stay out of every menu: the menu has /help, which leads to them.
    menus = {key: names for key, names in lists.items() if key.endswith("COMMANDS")}
    i18n_menu = command_menu(bot)
    in_menu = [f"{where}:{name}" for where, names in menus.items() for name in names
               if name.startswith("help_") or name == "fullhelp"]
    in_menu += [f"{lang}:{name}" for lang, table in i18n_menu.items() for name in table
                if name.startswith("help_") or name == "fullhelp"]
    check(f"{bot}: no help page and no /fullhelp is in any menu"
          + (f" -- {', '.join(in_menu)}" if in_menu else ""), not in_menu)

    check(f"{bot}: /fullhelp is handled", "fullhelp" in handlers)
    check(f"{bot}: the help page CallbackQueryHandler is registered with HELP_PAGE_PATTERN",
          registers_page_button(bot))
    stray = help_commands_registered(bot)
    check(f"{bot}: no help_<x> command is registered" + (f" -- {', '.join(stray)}" if stray else ""), not stray)


def check_parent():
    """ManagerBot, which is the same rule upside down.

    It is private and every command refuses a stranger, so there is no public
    half of the menu to keep honest -- the whole list is the owner's, and what
    goes up at the default scope is the one command that answers a stranger.
    It was left out of BOTS above because none of those checks fit it, which is
    how it went several versions with /probe, /providers, /stars and
    /crashtest handled and unlisted.
    """
    bot = "manager_bot"
    handlers, lists, _ = analyse(bot)
    menu = lists.get("BOT_COMMANDS", [])
    stranger = lists.get("STRANGER_COMMANDS", [])
    # Renamed in 1.7.0, with the old name still answering. The menu carries
    # the new one; listing both would offer the same thing twice, and dropping
    # the old one would break a habit for nothing.
    # Only what is a second name for something in the menu. It used to list
    # /providers, /probe and /stars here too, "reachable through /run" -- which
    # they are not in the way an alias is: /run needs the bot and the command
    # named, and the shortcuts exist so the owner need not. Calling them
    # aliases is what kept them out of the menu from 1.7.0, and it hid the
    # gap from this very check; the owner found it by typing "/pro" and being
    # offered /problems and nothing else (2026-09-29).
    aliases = {
        "finishupdates": "resume",
        "errorlog": "logs", "botlog": "logs", "problemlog": "logs",
        "me": "status",
    }
    # Handled, kept out of the menu, and not because it has another name.
    unlisted = {
        "crashtest": "makes a bot raise on purpose and page the owner, and a tap "
                     "in the menu sends the command at once -- a lever worth "
                     "typing, not one to leave under a thumb",
    }
    handlers = {c: fn for c, fn in handlers.items() if c not in aliases and c not in unlisted}

    check(f"{bot}: no command is listed twice", len(menu) == len(set(menu)))

    stale = [c for c in menu if c not in handlers]
    check(f"{bot}: every advertised command has a handler"
          + (f" -- {', '.join(stale)}" if stale else ""), not stale)

    # "Or the stranger's" covers /start, which is the same handler as /help.
    # A chat scope replaces the default rather than adding to it, so the owner
    # sees only BOT_COMMANDS -- and /help is /start under the name that says
    # what it does, which is the one worth spending a menu line on.
    missing = [c for c in sorted(handlers) if c not in menu and c not in stranger]
    check(f"{bot}: every command it handles is offered somewhere"
          + (f" -- {', '.join(missing)}" if missing else ""), not missing)

    # A stranger who finds the private bot should not be handed a list of the
    # levers. Every one of them refuses -- guard() decides, not the menu -- but
    # publishing them is an invitation with no upside.
    # An exception that stops being true is worse than none, as for the public
    # bots: it would hide a command that has since become worth offering.
    for cmd, why in unlisted.items():
        all_handlers = analyse(bot)[0]
        check(f"{bot}: /{cmd} is still handled and still kept out of the menu ({why[:36]}...)",
              cmd in all_handlers and cmd not in menu)

    # /reports was in the alias table above ("it is /problems now") for three
    # versions and so never offered, while /report beside it was. They are two
    # commands with two handlers: the list of recent problems, and one of them
    # in full. An alias is only an alias if it is registered as one.
    check(f"{bot}: /reports, the list that /report opens one row of, is in the menu", "reports" in menu)
    registered = analyse(bot)[0]
    check(f"{bot}: /reports is its own command, not a second name for /problems",
          registered.get("reports") not in (None, registered.get("problems")))
    check(f"{bot}: /nightly, which the owner types, is in the menu", "nightly" in menu)
    check(f"{bot}: /nightly really is registered with /problems",
          registered.get("nightly") == registered.get("problems") != None)  # noqa: E711
    check(f"{bot}: every alias named above is a handled command",
          all(alias in registered for alias in aliases))

    # The commands that reach one bot each: what is offered has to be the
    # command that runs, so a shortcut in the menu is a shortcut registered.
    for cmd in ("providers", "probe", "stars"):
        check(f"{bot}: /{cmd}, which reaches one bot, is in the menu", cmd in menu)

    check(f"{bot}: strangers are offered /start and nothing else",
          stranger == ["start"])
    for cmd in ("sql", "backup", "restart", "broadcast"):
        check(f"{bot}: /{cmd} is not published to strangers", cmd not in stranger)


def main() -> int:
    for bot in BOTS:
        print(f"\n== {bot} ==")
        check_public(bot)

    print("\n== manager_bot ==")
    check_parent()

    print("\n== what the popup shows ==")
    for bot in BOTS + ["manager_bot"]:
        check_visible(bot)

    print()
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for f in failures:
            print("  -", f)
        return 1
    print("every bot's menu matches what it handles")
    return 0


if __name__ == "__main__":
    sys.exit(main())
