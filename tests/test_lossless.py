"""/lossless has to survive Telegram, not just the bot.

The setting was saved, read and branched on correctly for months, and it still
did not work for videos, because `sendDocument` sniffs what it is given: hand
it an .mp4 and the Bot API decides it is really a video and delivers it as one
-- re-encoded, which is the single thing the setting exists to prevent. It
does not do that to a .jpg, so the image paths worked and the video path did
not, which is the worst shape a bug can have: the feature looks present,
half of it demonstrably works, and the broken half is only visible to whoever
turned it on.

`disable_content_type_detection=True` is the whole fix, and it is one keyword
that is easy to drop when a send is next edited. So it is checked here rather
than remembered.

Read with ast, so nothing has to be stubbed and no file is uploaded.
"""
import ast, pathlib, sys

REPO = pathlib.Path(__file__).resolve().parent.parent
BOT = REPO / "downloader_bot" / "bot.py"

# The functions that deliver a download to a user. Anything else sending a
# document here (the owner's database export, for instance) is not making a
# promise about fidelity and is none of this test's business.
#
# v1.2.1 collapsed three of these into one: _download_and_send_video and the
# per-platform photo sends became _send_media, which is now the single place
# a resolved download reaches Telegram. _handle_twitter no longer sends
# anything itself -- it delegates and then renders a card for text tweets,
# and the card goes out through _reply_image like every other card.
DELIVERY = {"_reply_image", "_send_media"}
SENDS = {"reply_document", "send_document", "InputMediaDocument"}

failures = []
def check(name, ok):
    print(("  ok  " if ok else "  FAIL  ") + name)
    if not ok:
        failures.append(name)


def flag_is_set(call):
    for kw in call.keywords:
        if kw.arg == "disable_content_type_detection":
            return isinstance(kw.value, ast.Constant) and kw.value.value is True
    return False


def main() -> int:
    tree = ast.parse(BOT.read_text(encoding="utf-8"))
    found = 0
    seen_functions = set()

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in DELIVERY:
            continue
        seen_functions.add(node.name)
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            name = (sub.func.id if isinstance(sub.func, ast.Name)
                    else getattr(sub.func, "attr", None))
            if name not in SENDS:
                continue
            found += 1
            check(f"{node.name}: {name} at line {sub.lineno} keeps Telegram's "
                  f"hands off the file", flag_is_set(sub))

    # If the delivery functions get renamed this test would quietly pass by
    # checking nothing at all, which is the failure mode a static test is
    # most prone to.
    missing = DELIVERY - seen_functions
    check("every delivery function was found"
          + (f" -- missing {', '.join(sorted(missing))}" if missing else ""), not missing)
    check(f"and they actually send documents ({found} found)", found >= 3)

    print()
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for f in failures:
            print("  -", f)
        return 1
    print("uncompressed really means uncompressed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
