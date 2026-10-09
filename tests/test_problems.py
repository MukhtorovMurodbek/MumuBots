"""The problem codes behind every error message and its Report button.

    python3 tests/test_problems.py

Every code decodes, every code is used, every message that carries one exists
in all three languages, a pop-up with its code still fits Telegram's 200
characters, a code is read back only from the end of a message, and the data
behind the longest Report button fits in 64 bytes.
"""
import ast
import pathlib
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE / ".mirror" / "convert_bot"))

import problems as p  # noqa: E402

PUBLIC = ("sticker_bot", "convert_bot", "downloader_bot", "anon_bot")
LANGS = ("en", "uz", "ru")
failures = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL  ") + name + ("" if ok else f"  -- {detail}"))
    if not ok:
        failures.append(name)


def strings(bot):
    tree = ast.parse((REPO / bot / "i18n.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            names = [k.value for k in node.keys if isinstance(k, ast.Constant)]
            if {"en", "uz", "ru"} <= set(names):
                table = {}
                for k, v in zip(node.keys, node.values):
                    if isinstance(k, ast.Constant) and k.value in LANGS and isinstance(v, ast.Dict):
                        table[k.value] = {kk.value: ast.literal_eval(vv) for kk, vv in zip(v.keys, v.values)
                                          if isinstance(kk, ast.Constant)}
                if len(table) == 3:
                    return table
    raise AssertionError(f"{bot}: no STRINGS table found")


print("== the registry ==")
malformed = [code for code in p.PROBLEMS if not p.CODE_RE.match(code)]
check("every code has the shape XX-WORD", not malformed, malformed)
empty = [code for code, problem in p.PROBLEMS.items() if not all(problem)]
check("every code says which bot, what happened, why, and what to check", not empty, empty)
used = set(p.ASSEMBLED)
for table in p.KEYS.values():
    used.update(table.values())
check("every code is used, and every code used is defined",
      used == set(p.PROBLEMS), sorted(used ^ set(p.PROBLEMS)))
check("every ending the conversion queue gives back credit for has a code",
      set(p.JOB_ENDINGS) == {"refused", "timeout", "too_slow", "crash", "no_memory", "too_large", "send_failed",
                             "interrupted"})

print("\n== which problems offer a Report button ==")
check("every problem kept from a Report button is a real code",
      p.SIMPLE <= set(p.PROBLEMS), sorted(p.SIMPLE - set(p.PROBLEMS)))
check("an unknown command is too simple for one (the owner's example)", not p.reportable("FM-UNKNOWN-COMMAND"))
check("a crash still offers one, and so does every conversion that failed on the bot's side",
      p.reportable("FM-CRASH") and all(p.reportable(code) for kind, code in p.JOB_ENDINGS.items()
                                        if kind not in ("too_slow", "no_memory")))
check("but not one stopped for being too long for the host -- a shorter clip is the person's to send",
      not p.reportable("CV-TOO-SLOW"))
check("nor one too big for the host's memory -- a lower resolution is the person's to send",
      not p.reportable("CV-NO-MEMORY"))
check("so does every sticker Telegram refused, bar a pack that is simply full, and the owner's own misconfiguration",
      all(p.reportable(code) for code in p.STICKER_ERRORS.values() if code != "ST-TG-PACK-FULL")
      and not p.reportable("ST-TG-PACK-FULL") and p.reportable("FM-DONATE-UNAVAILABLE"))
check("a code nobody defined offers nothing", not p.reportable("ZZ-NOPE"))
check("decoding says whether a Report button is offered",
      "Report button: not offered" in p.decode("FM-UNKNOWN-COMMAND")
      and "Report button: offered" in p.decode("FM-CRASH"))
print(f"  ({len(p.PROBLEMS) - len(p.SIMPLE)} codes offer a Report button, {len(p.SIMPLE)} do not)")

print("\n== the messages that carry them ==")
for bot in PUBLIC:
    table = strings(bot)
    keys = dict(p.KEYS["shared"])
    keys.update(p.KEYS[bot])
    if bot == "sticker_bot":
        keys.update(p.STICKER_ERRORS)
    missing = sorted({key for key in keys for lang in LANGS if key not in table[lang]})
    check(f"{bot}: every coded message exists in all three languages", not missing, missing)
    too_long = []
    for key in p.ALERT_KEYS["shared"] | p.ALERT_KEYS[bot]:
        for lang in LANGS:
            text = table[lang].get(key, "")
            # Placeholders are filled with short things -- a number, an
            # extension -- so a dozen characters of room covers them.
            length = len(text) + len(p.code_line(p.code_for(bot, key))) + 12
            if length > 200:
                too_long.append((key, lang, length))
    check(f"{bot}: every pop-up still fits 200 characters with its code", not too_long, too_long)
    for key in ("report_button", "problem_logged_note", "report_disclaimer", "report_send",
                "report_cancel", "report_sent", "report_already", "report_cancelled",
                "report_failed", "report_invalid", "crash_notice"):
        absent = [lang for lang in LANGS if key not in table[lang]]
        if absent:
            check(f"{bot}: the report flow's {key} exists in every language", False, absent)
            break
    else:
        check(f"{bot}: the report flow is written in all three languages", True)
    buttons = [(lang, len(table[lang]["report_button"])) for lang in LANGS]
    check(f"{bot}: the Report button's label is short enough for a button",
          all(n <= 32 for _, n in buttons), buttons)

print("\n== reading a code back ==")
text = "Something failed." + p.code_line("CV-TIMEOUT")
check("a code at the end of a message is found", p.find_code(text) == "CV-TIMEOUT")
check("inside closing HTML tags too", p.find_code("<b>Failed" + p.code_line("FM-ERASE") + "</b>") == "FM-ERASE")
check("not one in the middle of a message", p.find_code(text + "\n\nand more") is None)
check("and not a code nobody defined", p.find_code("Failed\n\n🆔 ZZ-NOPE") is None)
check("the code line comes off again", p.strip_code(text) == "Something failed.")
check("a key with no code adds nothing", p.code_line(p.code_for("convert_bot", "job_done")) == "")
check("a bot's own key wins over the shared table",
      p.code_for("downloader_bot", "download_failed") == "DL-DELIVERY"
      and p.code_for("convert_bot", "download_failed") == "CV-DOWNLOAD")

incidents = {p.new_incident() for _ in range(500)}
check("incidents are well formed", all(p.is_incident(i) for i in incidents))
check("and practically never repeat", len(incidents) > 495, len(incidents))
check("a malformed incident is refused", not p.is_incident("ABC") and not p.is_incident("ABCDE0"))
longest = max(p.PROBLEMS, key=len)
data = f"rpts:{longest}:{p.new_incident()}:{4_102_444_800}"
check(f"the longest Report button's data fits 64 bytes ({len(data.encode())})", len(data.encode()) <= 64)
check("decoding names what it is", p.PROBLEMS["CV-TIMEOUT"].title in p.decode("CV-TIMEOUT"))

print()
if failures:
    print(f"FAILURES ({len(failures)}):")
    for name in failures:
        print("  -", name)
    sys.exit(1)
print("every problem has a code that decodes")
