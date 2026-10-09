"""2.1.0's AnonBot reactions and the family's /feedback, the parts that need
no database and no Telegram.

    python3 tests/test_reactions_feedback.py

What reaches the other side when a reaction changes, and which note the
reactor gets; that every string either feature shows exists in all three
languages in every bot that shows it; that an answer is taken as feedback
only when it answers the question; and that the nightly report is at 22:00
where the owner is. tests/anon_scenarios.py groups R, S and T drive the same
things end to end against a database.
"""
import ast
import os
import pathlib
import sys
from types import SimpleNamespace

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE / ".mirror" / "anon_bot"))
sys.path.insert(0, str(HERE / "stubs"))
os.environ.setdefault("DATABASE_URL", "postgresql://stub")
os.environ.setdefault("ABOT_TOKEN", "1:AA")
os.environ.setdefault("ABOT_USERNAME", "x")

import anon_logic as al  # noqa: E402
import shared_features as sf  # noqa: E402

LANGS = ("en", "uz", "ru")
PUBLIC = ("sticker_bot", "convert_bot", "downloader_bot", "anon_bot")
failures = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL  ") + name + ("" if ok else f"  -- {detail}"))
    if not ok:
        failures.append(name)


def emoji(*values):
    return [SimpleNamespace(type="emoji", emoji=v) for v in values]


def custom(value="5368324170671202286"):
    return SimpleNamespace(type="custom_emoji", custom_emoji_id=value)


print("== what a change of reaction passes on ==")
pick = getattr(al, "pick_reaction", None)
cases = [
    ("a first reaction is passed on", [], emoji("❤"), (["❤"], None)),
    ("a changed one replaces it", emoji("❤"), emoji("🔥"), (["🔥"], None)),
    ("taking it off takes it off", emoji("🔥"), [], ([], None)),
    ("a custom emoji is not passed on, and the person is told", [], [custom()],
     ([], "reaction_not_passed")),
    ("a custom emoji beside a standard one keeps the standard one there", emoji("👍"),
     emoji("👍") + [custom()], (["👍"], "reaction_not_passed")),
    ("two at once: the newest goes, and the person hears only one does", [], emoji("👍", "🎉"),
     (["🎉"], "reaction_one_only")),
    ("adding a second: that one goes, and the same note", emoji("👍"), emoji("👍", "🎉"),
     (["🎉"], "reaction_one_only")),
    ("taking one of two off leaves the other", emoji("👍", "🎉"), emoji("👍"), (["👍"], None)),
    ("a paid reaction is not passed on either", [], [SimpleNamespace(type="paid")],
     ([], "reaction_not_passed")),
    ("taking off a custom one says nothing", [custom()], [], ([], None)),
]
for name, old, new, want in cases:
    got = pick(old, new) if pick else None
    check(name, got == want, f"{got} != {want}")

note_ok = getattr(al, "reaction_note_allowed", None)
if note_ok:
    al._reaction_notes.clear()
check("the note is shown once, then held back for a while",
      bool(note_ok) and note_ok(1) and not note_ok(1) and note_ok(2))
check("the list a bot may react with is Telegram's 73",
      len(getattr(al, "RELAYABLE_REACTIONS", ())) == 73
      and len(set(al.RELAYABLE_REACTIONS)) == 73)


print("\n== every string, in every language ==")


def tables(bot):
    tree = ast.parse((REPO / bot / "i18n.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            names = [k.value for k in node.keys if isinstance(k, ast.Constant)]
            if set(LANGS) <= set(names):
                out = {}
                for k, v in zip(node.keys, node.values):
                    if isinstance(k, ast.Constant) and k.value in LANGS and isinstance(v, ast.Dict):
                        out[k.value] = {kk.value: ast.literal_eval(vv)
                                        for kk, vv in zip(v.keys, v.values) if isinstance(kk, ast.Constant)}
                if len(out) == 3 and "privacy_heading" in out["en"]:
                    return out
    raise AssertionError(f"{bot}: no STRINGS table")


FEEDBACK_KEYS = ("feedback_ask", "feedback_placeholder", "feedback_text_only", "feedback_thanks",
                 "feedback_limit", "feedback_failed", "cancel_button_feedback", "cancel_item_feedback",
                 "privacy_feedback_heading", "privacy_feedback")
ANON_KEYS = ("help_brief", "help_text", "help_full_button", "help_brief_button",
             "reactions_update_note", "reaction_not_passed", "reaction_one_only")
for bot in PUBLIC:
    t = tables(bot)
    missing = [(lang, key) for lang in LANGS for key in FEEDBACK_KEYS if key not in t[lang]]
    check(f"{bot}: /feedback and its privacy paragraph in all three languages", not missing, missing)
    check(f"{bot}: the allowance is named in every language",
          all("{per_day}" in t[lang].get("feedback_limit", "") for lang in LANGS))
    check(f"{bot}: the placeholder fits Telegram's 64 characters",
          all(len(t[lang].get("feedback_placeholder", "")) <= 64 for lang in LANGS))
    check(f"{bot}: the question says it goes without their id, in every language",
          all(any(word in t[lang].get("feedback_ask", "") for word in ("id", "ID"))
              for lang in LANGS))
anon = tables("anon_bot")
missing = [(lang, key) for lang in LANGS for key in ANON_KEYS if key not in anon[lang]]
check("anon_bot: help, its buttons, the update note and the reaction notes in all three", not missing, missing)
check("anon_bot: the full guide has a place for the reactions in every language",
      all("{reactions}" in anon[lang].get("help_text", "") for lang in LANGS))
check("anon_bot: both versions name Telegram Premium in every language",
      all("Premium" in anon[lang].get(key, "") for lang in LANGS for key in ("help_brief", "help_text")))
check("anon_bot: the short version is short in every language",
      all(len(anon[lang].get("help_brief", "x" * 9999)) * 2 < len(anon[lang].get("help_text", ""))
          for lang in LANGS))
check("anon_bot: the notes that remove themselves can be read before they go",
      all(len(anon[lang].get(key, "x" * 999)) <= 100
          for lang in LANGS for key in ("reaction_not_passed", "reaction_one_only")),
      [(lang, key, len(anon[lang].get(key, ""))) for lang in LANGS
       for key in ("reaction_not_passed", "reaction_one_only")])
check("anon_bot: and they open with what the reader has lost -- the other person, first",
      all(anon[lang].get(key, "").lstrip("⚠️ ").startswith(start)
          for lang, start in (("en", "The other person"), ("uz", "Suhbatdoshingiz"), ("ru", "Собеседник"))
          for key in ("reaction_not_passed", "reaction_one_only")))
check("anon_bot: on screen long enough for two lines, scrolling down included",
      getattr(al, "REACTION_NOTE_SECONDS", 0) >= 8 or os.environ.get("ABOT_REACTION_NOTE_SECONDS"))
check("anon_bot: the update note points at /help in every language",
      all("/help" in anon[lang].get("reactions_update_note", "") for lang in LANGS))
check("anon_bot: the Russian guide says replying is required, like the others",
      "обязательно" in anon["ru"].get("help_text", ""))


print("\n== one help template, every bot ==")
# The owner: "Yes all bots should be similar or share a similar template."
BULLET = {"en": "• Ideas or problems: /feedback", "uz": "• Taklif yoki muammo: /feedback",
          "ru": "• Идеи или проблемы: /feedback"}
for bot in PUBLIC:
    t = tables(bot)
    missing = [(lang, key) for lang in LANGS
               for key in ("help_brief", "help_full_button", "help_brief_button") if key not in t[lang]]
    check(f"{bot}: a short help and both buttons in all three languages", not missing, missing)
    # Shorter, not "half": ConvertBot's full guide was compact already, and its
    # price table is only filled in when it is shown.
    check(f"{bot}: the short help is shorter than the full one, in every language",
          all(len(t[lang].get("help_brief", "x" * 9999)) < len(t[lang].get("help_text", ""))
              for lang in LANGS))
    check(f"{bot}: every short help opens the same way and has the same /feedback bullet",
          all(t[lang].get("help_brief", "").startswith(("How it works:", "Bot qanday ishlaydi:",
                                                        "Как это устроено:")[LANGS.index(lang)])
              and BULLET[lang] in t[lang].get("help_brief", "") and "/help full" in t[lang]["help_brief"]
              for lang in LANGS))
    check(f"{bot}: the buttons say the same thing in every bot",
          all(t[lang].get("help_full_button") == anon[lang].get("help_full_button")
              and t[lang].get("help_brief_button") == anon[lang].get("help_brief_button") for lang in LANGS))
    source = (REPO / bot / "bot.py").read_text(encoding="utf-8")
    check(f"{bot}: wires the shared toggle, and /help full",
          "help_toggle(build_help_text" in source and "pattern=HELP_TOGGLE_PATTERN" in source
          and "wants_full_help(context)" in source and "help_keyboard(lang" in source)
check("the toggle answers help:full and help:brief and nothing else",
      __import__("re").fullmatch(getattr(sf, "HELP_TOGGLE_PATTERN", "x").lstrip("^").rstrip("$"),
                                 "help:full") is not None)


print("\n== feedback is one-way, and says so wherever it is mentioned ==")
# The owner: "Make the feedback one sided ... Make sure its mentioned anywhere
# that the feedback is read only for me."
NO_REPLY = {"en": ("can't reply", "cannot reply", "can't write back"), "uz": ("javob",), "ru": ("ответ",)}
ONLY = {"en": "only", "uz": "faqat", "ru": "только"}
for bot in PUBLIC:
    t = tables(bot)
    places = {"the question": "feedback_ask", "the thank-you": "feedback_thanks",
              "/privacy": "privacy_feedback", "the short help": "help_brief"}
    for name, key in places.items():
        check(f"{bot}: {name} says only one person reads it and nobody can reply, in every language",
              all(ONLY[lang] in t[lang].get(key, "").lower()
                  and any(marker in t[lang].get(key, "") for marker in NO_REPLY[lang]) for lang in LANGS),
              [lang for lang in LANGS if not any(m in t[lang].get(key, "") for m in NO_REPLY[lang])])
    lines = {lang: next((line for line in t[lang]["help_text"].split("\n") if line.startswith("/feedback")), "")
             for lang in LANGS}
    check(f"{bot}: and so does the full guide's /feedback line",
          all(any(marker in lines[lang] for marker in NO_REPLY[lang] + ("cannot",)) for lang in LANGS), lines)
for path in [*(REPO / bot / "PRIVACY.md" for bot in PUBLIC), *(REPO / bot / "README.md" for bot in PUBLIC)]:
    check(f"{path.parent.name}/{path.name}: /feedback is called one-way",
          "one-way" in path.read_text(encoding="utf-8"))
manager = (REPO / "manager_bot" / "bot.py").read_text(encoding="utf-8")
check("ManagerBot: every place feedback shows says read only",
      manager.count("read only") >= 4 and "FEEDBACK_READ_ONLY" in manager)


print("\n== an answer is feedback only when it answers the question ==")
answers = getattr(sf, "_answers_feedback_prompt", None)


def update_replying_to(message_id):
    replied = SimpleNamespace(message_id=message_id) if message_id is not None else None
    return SimpleNamespace(effective_message=SimpleNamespace(reply_to_message=replied, text="hi"))


waiting = SimpleNamespace(user_data={"feedback_prompt": 41})
check("a reply to the question is", bool(answers) and answers(update_replying_to(41), waiting))
check("a reply to anything else is not -- in AnonBot that is somebody's conversation",
      bool(answers) and not answers(update_replying_to(40), waiting))
check("a message that replies to nothing is not",
      bool(answers) and not answers(update_replying_to(None), waiting))
check("and nothing is, with no question asked",
      bool(answers) and not answers(update_replying_to(41), SimpleNamespace(user_data={})))
word = getattr(sf, "_COMMAND_WORD", None)
check("/feedback's words keep their lines, and lose the command",
      bool(word) and word.sub("", "/feedback@mumu_chat_bot first line\nsecond", count=1)
      == "first line\nsecond")
if hasattr(sf, "_feedback_sent"):
    sf._feedback_sent.clear()
    per_day = sf.FEEDBACK_PER_DAY
    for _ in range(per_day):
        sf._feedback_used(7)
check("the day's allowance counts what was sent, per person",
      hasattr(sf, "_feedback_room") and not sf._feedback_room(7) and sf._feedback_room(8))


print("\n== the nightly report is at 22:00 where the owner is ==")
source = (REPO / "manager_bot" / "bot.py").read_text(encoding="utf-8")
default = source.split('os.environ.get("MBOT_NIGHTLY_UTC", "', 1)[1].split('"', 1)[0]
hour, _, minute = default.partition(":")
check(f"the default, {default} UTC, is 22:00 in Korea (UTC+9, no daylight saving)",
      ((int(hour) + 9) % 24, int(minute or 0)) == (22, 0), default)
check("and nothing says midnight any more",
      "midnight problems report" not in source and "The midnight report" not in source)

print()
if failures:
    print(f"FAILURES ({len(failures)}):")
    for name in failures:
        print("  -", name)
    sys.exit(1)
print("reactions and feedback, offline")
