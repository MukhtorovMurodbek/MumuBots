"""Button taps are counted, and the count says nothing about who.

    python tests/test_button_taps.py              the counting, in the shared module every public bot has
    python tests/test_button_taps.py manager      ManagerBot's /buttons, which reads the counts back

The owner asked (2026-10-12) to measure "any buttons that we might need info about". The answer is a
count per button per day for the whole bot, kept in family.button_taps, and nothing about who tapped:
no user id, no chat id, no token, no link, no job id, no pack name, no conversation id and no amount is
ever part of a key, a log line or a row. One hook counts every public bot's buttons: track_activity in
shared/shared_features.py sees every update before any handler does. ManagerBot's own buttons are the
owner's and are not counted. What is checked here, with no database (the rows are tests/taps_db_scenarios.py):

  * tap_key on every callback_data the bots build -- read out of their sources, with each placeholder
    (a token, an id, a pack name, an amount...) replaced by a marker, so that a marker turning up in a
    key is a leak: the real shapes of all five bots, the real format targets and categories and the real
    languages; a first part that is not a button's name is "other"; a second part is kept only for the
    buttons whose second part is a choice from a short fixed list, and only if it is exactly one of them;
  * note_tap: counts, at most 150 distinct keys between two flushes (the rest are "other"), no raising;
  * track_activity counts a tap of a button and nothing else (a message, a game, a button with no data
    are not counted), is not stopped by a counter that raises, and ManagerBot (which has its own
    track_activity, and no shared_features) counts nothing;
  * the flush: the counts are swapped out on the event loop before the thread writes them, so a tap that
    lands while the write is going on is counted in the next window and not lost; a failed write is
    logged by kind and nothing else, and the job survives it;
  * /buttons: nothing recorded, one bot, DownloaderBot's fixed block, the 25-line cap, the family's order,
    bad days, who may ask.

To see a check catch breakage, copy the bot into tests/.mirror, break the copy and run this again: it
imports from the mirror when there is one.
"""
import ast
import asyncio
import logging
import os
import pathlib
import re
import sys
import tempfile
import threading
from types import SimpleNamespace

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
MODE = sys.argv[1] if len(sys.argv) > 1 else "taps"
FOLDER = "manager_bot" if MODE == "manager" else "downloader_bot"
SRC = HERE / ".mirror" / FOLDER
if not SRC.exists():
    SRC = REPO / FOLDER
sys.path.insert(0, str(SRC))
sys.path.insert(1, str(REPO / "shared"))
sys.path.insert(0, str(HERE / "stubs"))

SCRATCH = pathlib.Path(tempfile.mkdtemp(prefix="taps-"))
for var in ("SBOT", "CBOT", "DBOT", "ABOT", "MBOT"):
    os.environ[f"{var}_TOKEN"] = "123:test"
    os.environ[f"{var}_USERNAME"] = "test"
    os.environ[f"{var}_ADMIN_ID"] = "1"
os.environ.update(ENV_FILES="off", DATABASE_URL="postgresql://stub", DBOT_WORK_DIR=str(SCRATCH), MBOT_REGISTRY="")

logging.disable(logging.CRITICAL)
failures = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL  ") + name + (f" -- {detail}" if not ok and detail else ""))
    if not ok:
        failures.append(name)


def run(coro):
    return asyncio.run(coro)


MARK = "ZQX"          # a marker no key may contain: every placeholder in a callback_data becomes MARK<n>


# ---------------------------------------------------------------------------
# the callback_data every bot builds, read out of its source
# ---------------------------------------------------------------------------

def module_strings(tree):
    """NAME = "text" at the top of a module (the *_PREFIX constants)."""
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found[target.id] = node.value.value
    return found


def render(node, names, counter):
    """The text an expression builds, with a marker where a value is not known until run time."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in names:
            return names[node.id]
        counter[0] += 1
        return f"{MARK}{counter[0]}"
    if isinstance(node, ast.JoinedStr):
        return "".join(render(part.value, names, counter) if isinstance(part, ast.FormattedValue)
                       else part.value for part in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return render(node.left, names, counter) + render(node.right, names, counter)
    counter[0] += 1
    return f"{MARK}{counter[0]}"


def real_callback_data():
    """{file: [callback_data as it would be, markers for the unknown parts]} for every bot and the shared module."""
    found = {}
    for path in [*(REPO / "shared").glob("*.py")] + [p for bot in
                                                       ("convert_bot", "downloader_bot", "sticker_bot", "anon_bot")
                                                       for p in (REPO / bot).glob("*.py")]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = module_strings(tree)
        counter = [0]
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "callback_data":
                found.setdefault(f"{path.parent.name}/{path.name}", []).append(render(node.value, names, counter))
    return found


def taps_checks():
    import shared_features as sf
    import family_link
    key = sf.tap_key

    print("\n== the keys of every real button ==")
    shapes = real_callback_data()
    # (a callback_data that is nothing but a variable is a button built elsewhere, from a value this cannot know)
    every = [data for datas in shapes.values() for data in datas if not re.fullmatch(MARK + r"\d+", data)]
    check("found the buttons of all five bots' sources to check", sum(1 for d in every) > 70
          and {"shared", "convert_bot", "downloader_bot", "sticker_bot", "anon_bot"} <= {f.split("/")[0] for f in shapes},
          (len(every), sorted(shapes)))
    leaks, odd = [], []
    for data in every:
        got = key(data)
        if MARK in got:
            leaks.append((data, got))
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,23}(:[A-Za-z0-9_]{1,16})?", got) and got != "other":
            odd.append((data, got))
    check("no placeholder (token, id, name, amount) of any real callback_data reaches its key", not leaks, leaks[:5])
    check("every key is a button's name, or its name and one short choice", not odd, odd[:5])
    check("and none of the real buttons is 'other'", not [d for d in every if key(d) == "other"],
          [d for d in every if key(d) == "other"][:5])

    expected = {
        "help:full": "help:full", "help:brief": "help:brief", "setlang:uz": "setlang:uz", "setlang:ru": "setlang:ru",
        "setlang:en": "setlang:en", "erasedata:yes": "erasedata:yes", "erasedata:no": "erasedata:no",
        "donate:15": "donate:15", "donate:500": "donate:500", "donatefiat:USD:5": "donatefiat:USD",
        "donatecustom:XTR": "donatecustom:XTR", "donatecustom:USD": "donatecustom:USD",
        "caption:on": "caption:on", "caption:off": "caption:off", "lossless:on": "lossless:on",
        "large:on": "large:on", "large:off": "large:off", "large:offer": "large:offer",
        "merge:pdf:0123456789": "merge:pdf", "merge:zip:0123456789": "merge:zip",
        "redo:0123456789": "redo", "convfmt:jpg": "convfmt:jpg", "fmtcat:image": "fmtcat:image",
        "aq_export:pick:123456": "aq_export:pick", "aq_export:copy:all": "aq_export:copy",
        "aq_export:doc:all": "aq_export:doc", "aq_export:cancel": "aq_export:cancel",
        "anonlink:toggle": "anonlink:toggle", "anonlink:newlink": "anonlink:newlink",
        "confirmremove:yes": "confirmremove:yes", "confirmremove:no": "confirmremove:no",
        "convmore": "convmore", "convgo": "convgo", "fmtall": "fmtall", "menu_newpack": "menu_newpack",
        "packopen:my_pack_by_bot": "packopen", "pack:my_pack_by_bot": "pack", "packdel:x": "packdel",
        "convstop:9f8e7d": "convstop", "cancelpick:all": "cancelpick", "cancelpick:abc123": "cancelpick",
        "rpt:ERR-1234:INC-5678": "rpt", "rpts:ERR-1234:INC-5678:1760000000": "rpts", "rptc": "rptc",
        "aq_conv:123:0:2": "aq_conv", "aq_convs:1:3": "aq_convs", "aq_reply:123": "aq_reply",
        "aq_block:123": "aq_block", "board:refresh": "board",
        "helpdet:convert": "helpdet:convert", "helpdet:which": "helpdet:which",
        "helpdet:nosuchpage": "helpdet", "helpdet:Convert": "helpdet", "helpdet:": "helpdet",
    }
    wrong = {data: (key(data), want) for data, want in expected.items() if key(data) != want}
    check("the shapes the bots send give exactly the keys the owner will read", not wrong, wrong)

    convert = HERE / ".mirror" / "convert_bot"
    sys.path.append(str(convert if convert.exists() else REPO / "convert_bot"))
    import formats
    targets = set()
    for group in (*formats.TARGETS_BY_CATEGORY.values(), *formats.TARGETS_BY_FORMAT.values(), formats.ANIMATED_TARGETS):
        targets |= set(group)
    lost = sorted(t for t in targets if key(f"convfmt:{t}") != f"convfmt:{t}")
    check(f"every one of ConvertBot's {len(targets)} real format targets keeps its name", not lost, lost)
    lost = sorted(c for c in formats.CATEGORIES if key(f"fmtcat:{c}") != f"fmtcat:{c}")
    check("and every category", not lost, lost)
    import i18n
    lost = sorted(c for c in i18n.LANGUAGE_LABELS if key(f"setlang:{c}") != f"setlang:{c}")
    check("and every language", not lost, lost)
    lost = [a for a in sf.DONATE_STAR_OPTIONS if key(f"donate:{a}") != f"donate:{a}"]
    check("and every Stars amount of the donation menu", not lost, lost)
    # The help pages are read out of the bots' sources (HELP_PAGES is a dict literal), so a page added
    # to a bot is counted under its own name only once it is added to _TAP_SECOND as well.
    pages = []
    for bot in ("convert_bot", "downloader_bot", "sticker_bot", "anon_bot"):
        tree = ast.parse((REPO / bot / "bot.py").read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "HELP_PAGES" for t in node.targets):
                pages += [k.value for k in node.value.keys]
    lost = [p for p in pages if key(f"{sf.HELP_PAGE_PREFIX}{p}") != f"helpdet:{p}"]
    check(f"and every one of the {len(pages)} help pages of the four public bots", pages and not lost, lost)
    lost = [(c, a) for c, spec in sf.FIAT_CURRENCIES.items() for a in spec["options"]
            if key(f"donatefiat:{c}:{a}") != f"donatefiat:{c}"]
    check("and a card donation is counted by its currency, never its amount", not lost, lost)

    print("\n== what is never a key ==")
    secret = "a9f3c2d41b"
    for data, want in (("redo:" + secret, "redo"), ("merge:pdf:" + secret, "merge:pdf"), ("merge:rar:" + secret, "merge"),
                       ("setlang:UZ", "setlang"), ("setlang:uzbek", "setlang"), ("setlang:u", "setlang"),
                       ("help:full\n", "help"), ("help:fullx", "help"), ("help:", "help"), ("donate:16", "donate"),
                       ("donate:15:1", "donate:15"), ("donate:15\n", "donate"), ("donatefiat:US", "donatefiat"),
                       ("donatefiat:12$", "donatefiat"), ("donatecustom:узб", "donatecustom"),
                       ("convfmt:tar.gz", "convfmt"), ("convfmt:" + "a" * 11, "convfmt"), ("fmtcat:Image", "fmtcat"),
                       ("caption:maybe", "caption"), ("erasedata:YES", "erasedata"), ("large:ON", "large"),
                       ("aq_export:pick\n", "aq_export"), ("anonlink:toggle2", "anonlink"),
                       ("confirmremove:y", "confirmremove"), ("packopen:" + secret, "packopen"),
                       ("unknownbutton:" + secret, "unknownbutton")):
        got = key(data)
        check(f"{data!r} -> {want!r}", got == want and secret not in got, got)
    for data in ("", ":", ":x", "Redo:abc", "1abc", "a" * 25, "bad-name", "bad name:x", "редо:1",
                 "$rm -rf", "a\nb", "redo\n", "A", "_x", None, 5, b"redo:1", ["redo"], 1.5, True):
        check(f"{data!r} (not a button's name) -> 'other'", key(data) == "other", key(data))
    check("a name of exactly 24 characters is a name", key("a" * 24) == "a" * 24 and key("a" * 25) == "other")
    check("a very long callback_data is cheap and still safe", key("redo:" + "x" * 100000) == "redo")

    print("\n== note_tap ==")
    sf._taps.clear()
    for _ in range(3):
        sf.note_tap("help:full")
    sf.note_tap("redo")
    check("counts per key", sf._taps == {"help:full": 3, "redo": 1}, sf._taps)
    check("take_taps gives the counts and starts again", sf.take_taps() == {"help:full": 3, "redo": 1} and sf._taps == {})
    for n in range(300):
        sf.note_tap(f"button{n}")
    check("at most 150 distinct keys between two flushes", len(sf._taps) <= sf.TAP_MAX_KEYS == 150, len(sf._taps))
    check("nothing is lost: the rest are counted as 'other'",
          sum(sf._taps.values()) == 300 and sf._taps.get("other", 0) >= 300 - 150, (sum(sf._taps.values()), sf._taps.get("other")))
    sf.note_tap("button0")
    sf.note_tap("other")
    check("a key already in is still counted past the cap", sf._taps["button0"] == 2 and len(sf._taps) <= 150)
    check("'other' itself still counts past the cap", sf._taps["other"] >= 151)
    sf.take_taps()
    for bad in (None, 5, ["x"], {"a": 1}):
        try:
            sf.note_tap(bad)
            ok = True
        except Exception:
            ok = False
        check(f"note_tap({bad!r}) does not raise", ok)
    sf.take_taps()

    print("\n== track_activity ==")

    def update(callback=None, data="help:full", user=7, message=False):
        query = SimpleNamespace(data=data) if callback is None else callback
        return SimpleNamespace(callback_query=query, message=None, edited_message=None, effective_message=None,
                               effective_user=SimpleNamespace(id=user), effective_chat=SimpleNamespace(id=user))

    context = SimpleNamespace(user_data={}, application=SimpleNamespace(), bot=SimpleNamespace())
    family_link._bot_id = "downloaderbot"
    sf.take_taps()
    run(sf.track_activity(update(data="help:full"), context))
    run(sf.track_activity(update(data="merge:pdf:abcdef0123"), context))
    check("a tap of a button is counted under its key", sf._taps == {"help:full": 1, "merge:pdf": 1}, sf._taps)
    check("...and the user was still tracked (the rest of track_activity ran)", 7 in sf._activity_buffer)
    sf.take_taps()
    run(sf.track_activity(update(callback=None, data=None), context))
    run(sf.track_activity(SimpleNamespace(callback_query=None, message=None, edited_message=None, effective_message=None,
                                          effective_user=SimpleNamespace(id=8), effective_chat=SimpleNamespace(id=8)), context))
    run(sf.track_activity(SimpleNamespace(callback_query=SimpleNamespace(data=None), message=None, edited_message=None,
                                          effective_message=None, effective_user=SimpleNamespace(id=8),
                                          effective_chat=SimpleNamespace(id=8)), context))
    run(sf.track_activity(SimpleNamespace(callback_query=SimpleNamespace(data=b"help:full"), message=None, edited_message=None,
                                          effective_message=None, effective_user=SimpleNamespace(id=8),
                                          effective_chat=SimpleNamespace(id=8)), context))
    check("a message, a button with no data and data that is not text are not counted", sf._taps == {}, sf._taps)
    family_link._bot_id = "managerbot"
    run(sf.track_activity(update(data="help:full"), context))
    check("ManagerBot's id counts nothing, if this module were ever to run there", sf._taps == {}, sf._taps)
    family_link._bot_id = None
    run(sf.track_activity(update(data="help:full"), context))
    check("(and a bot with no id yet counts, as it must at start-up)", sf._taps == {"help:full": 1}, sf._taps)
    sf.take_taps()
    family_link._bot_id = "downloaderbot"

    real_note, real_key = sf.note_tap, sf.tap_key
    for name in ("note_tap", "tap_key"):
        setattr(sf, name, lambda *a, **k: (_ for _ in ()).throw(RuntimeError("counter broke")))
        sf._activity_buffer.discard(9)
        try:
            run(sf.track_activity(update(data="help:full", user=9), context))
            survived = True
        except Exception:
            survived = False
        finally:
            sf.note_tap, sf.tap_key = real_note, real_key
        check(f"a {name} that raises does not stop the update", survived and 9 in sf._activity_buffer)

    manager_src = (REPO / "manager_bot" / "monitoring.py").read_text(encoding="utf-8") + (REPO / "manager_bot" / "bot.py").read_text(encoding="utf-8")
    check("ManagerBot's own track_activity (monitoring.py) never counts: neither counter is even imported there",
          "note_tap" not in manager_src and "tap_key" not in manager_src)
    mirror = HERE / ".mirror" / "manager_bot"
    check("and ManagerBot is not given shared_features at all", not (mirror / "shared_features.py").exists()
          and not (REPO / "manager_bot" / "shared_features.py").exists())

    print("\n== the flush ==")
    written, order = [], []

    def fake_numbers():
        return {"rss_mb": 50, "peak_rss_mb": 60, "ceiling_mb": 512, "cpu_seconds": 1}

    real = (sf.footprint_numbers, family_link.record_usage, family_link.record_taps, sf._check_usage_alarms)
    sf.footprint_numbers, sf._check_usage_alarms = fake_numbers, lambda *a, **k: None
    family_link.record_usage = lambda *a, **k: order.append("usage")

    threads = {"take": [], "write": []}
    real_take = sf.take_taps

    def spying_take():
        threads["take"].append(threading.get_ident())
        return real_take()

    def writing(taps):
        order.append("write")
        threads["write"].append(threading.get_ident())
        sf.note_tap("tapped_during_the_write")      # the event loop is free while a thread writes
        written.append(dict(taps))

    family_link.record_taps = writing
    sf.take_taps = spying_take
    try:
        sf.take_taps()
        threads["take"].clear()
        sf.note_tap("help:full")
        sf.note_tap("help:full")
        sf.note_tap("redo")
        run(sf._usage_sample_job(None))
        check("swapped out on the event loop's thread, where the taps are counted, and written on a worker's",
              threads["take"] == [threading.get_ident()] and threads["write"] and threads["write"][0] != threading.get_ident(),
              threads)
        check("the window's counts are what is written", written == [{"help:full": 2, "redo": 1}], written)
        check("written with the usage sample, after the usage row", order == ["usage", "write"], order)
        check("a tap that lands during the write is not written, and not lost: it opens the next window",
              sf._taps == {"tapped_during_the_write": 1}, sf._taps)
        check("a second window writes it, and only it", (run(sf._usage_sample_job(None)), written[-1])[1]
              == {"tapped_during_the_write": 1} and sf._taps == {"tapped_during_the_write": 1})
        sf.take_taps()
        sf.note_tap("lossless:on")
        written.clear()
        sf._sample_usage_now()
        check("a caller with no counts of its own (the old way of calling it) takes them itself",
              written == [{"lossless:on": 1}], written)

        records = []

        class Catch(logging.Handler):
            def emit(self, record):
                records.append((record.getMessage(), bool(record.exc_info)))

        handler = Catch()
        logging.disable(logging.NOTSET)
        root = logging.getLogger()
        kept_handlers, kept_level = root.handlers[:], root.level
        root.handlers, root.level = [handler], logging.DEBUG

        def failing(taps):
            raise ConnectionError("password authentication failed for user secret-user at 10.1.2.3 pack_x_by_bot")

        family_link.record_taps = failing
        try:
            sf.take_taps()
            sf.note_tap("redo")
            run(sf._usage_sample_job(None))
            survived = True
        except Exception:
            survived = False
        finally:
            root.handlers, root.level = kept_handlers, kept_level
            logging.disable(logging.CRITICAL)
        text = "\n".join(message for message, _ in records)
        check("a write that fails does not stop the job", survived)
        check("it is logged by kind only: no text of the error, no traceback",
              "ConnectionError" in text and "secret-user" not in text and "10.1.2.3" not in text
              and not any(attached for _, attached in records), text)
        check("the failed window is not put back (the same as the usage row's own counts): nothing is left to double-count",
              sf._taps == {}, sf._taps)
    finally:
        sf.footprint_numbers, family_link.record_usage, family_link.record_taps, sf._check_usage_alarms = real
        sf.take_taps = real_take
        sf.take_taps()


# ---------------------------------------------------------------------------
# ManagerBot: /buttons
# ---------------------------------------------------------------------------

def manager_checks():
    import bot
    import family_link

    def rows(**per_bot):
        return [{"bot_id": b, "button": k, "taps": n} for b, counts in per_bot.items() for k, n in counts.items()]

    served = {"rows": [], "asked": []}

    def history(days=7):
        served["asked"].append(days)
        return served["rows"]

    family_link.button_taps_history = history
    report = bot._buttons_report

    print("\n== /buttons: the report ==")
    served["rows"] = []
    check("nothing recorded yet", report(7) == "No button taps recorded yet.", report(7))

    served["rows"] = rows(convertbot={"help:full": 3, "convgo": 10, "setlang:uz": 3})
    text = report(7)
    check("the header says what this is and what it is not",
          text.splitlines()[0] == "Button taps, last 7 days (counts only, nothing about who)", text)
    check("one bot: its display name, then its keys by taps, the most first, ties by name",
          text.splitlines()[2:] == ["ConvertBot", "  convgo: 10", "  help:full: 3", "  setlang:uz: 3"], text)
    check("and the days it was asked for are in the header", report(30).startswith("Button taps, last 30 days"))

    served["rows"] = rows(anonbot={"a": 1}, downloaderbot={"b": 1}, stickerbot={"c": 1}, convertbot={"d": 1}, oddbot={"e": 1})
    names = [line for line in report(7).splitlines() if line and not line.startswith(("  ", "Button"))]
    check("bots in the family's usual order (Sticker, Convert, Downloader, Anon), a bot nobody knows after them, by its id",
          names == ["StickerBot", "ConvertBot", "DownloaderBot", "AnonBot", "oddbot"], names)

    served["rows"] = rows(downloaderbot={
        "redo.to_file": 12, "redo.to_compressed": 4, "redo.expired": 3, "redo.spent": 2,
        "redo.after.lt1m": 5, "redo.after.1-2m": 4, "redo.after.2-5m": 3, "redo.after.5-15m": 2,
        "redo.after.15-60m": 1, "redo.after.gt60m": 1, "redo.after.unknown": 0,
        "redo.cache.hit": 9, "redo.cache.miss": 7, "cache.kept": 40, "cache.not_kept": 6,
        "link.again.cached": 11, "link.again.not_cached": 8,
        "help:full": 5, "merge:pdf": 2, "merge.expired": 1})
    lines = report(7).splitlines()
    block = lines[lines.index("DownloaderBot") + 1:]
    check("DownloaderBot first shows its fixed block, exactly as the owner specified it",
          block[:4] == [
              "  Redo taps: 16 (to file 12, to compressed 4); expired 3, double 2",
              "  Tap after: <1m 5 · 1-2m 4 · 2-5m 3 · 5-15m 2 · 15-60m 1 · >60m 1 · unknown 0",
              "  Redo from cache: hit 9 · miss 7; deliveries kept 40 · not kept 6",
              "  Same link again: cached 11 · not cached 8"], block[:4])
    check("then its other keys, without the ones the block already shows",
          block[4:] == ["  help:full: 5", "  merge:pdf: 2", "  merge.expired: 1"], block[4:])

    served["rows"] = rows(downloaderbot={"help:full": 1})
    block = report(7).splitlines()
    check("a DownloaderBot with nothing counted of those still shows the block, with zeros",
          "  Redo taps: 0 (to file 0, to compressed 0); expired 0, double 0" in block
          and "  Same link again: cached 0 · not cached 0" in block)

    served["rows"] = rows(stickerbot={f"key{n:02d}": 100 - n for n in range(30)})
    lines = report(7).splitlines()
    shown = [line for line in lines if line.startswith("  key")]
    check("at most 25 keys per bot, then how many more", len(shown) == 25 and lines[-1] == "  +5 more"
          and shown[0] == "  key00: 100" and shown[-1] == "  key24: 76", (len(shown), lines[-2:]))
    served["rows"] = rows(stickerbot={f"key{n:02d}": 5 for n in range(25)})
    check("exactly 25 keys: no 'more' line", not any("more" in line for line in report(7).splitlines()))
    served["rows"] = rows(downloaderbot={f"key{n:02d}": 100 - n for n in range(27)}, convertbot={"x": 1})
    lines = report(7).splitlines()
    check("the cap is per bot, and the fixed block does not count against it",
          sum(line.startswith("  key") for line in lines) == 25 and "  +2 more" in lines and "  x: 1" in lines)
    text = report(7)
    check("plain text only: nothing that Telegram would read as markup", "<b>" not in text and "<i>" not in text)

    print("\n== /buttons: the command ==")

    sent = []

    class Context:
        def __init__(self, args):
            self.args = args
            self.bot = SimpleNamespace(send_message=self._send, send_document=self._document)

        async def _send(self, chat_id=None, text=None, **kwargs):
            sent.append((text, kwargs))

        async def _document(self, chat_id=None, document=None, filename=None, **kwargs):
            sent.append(("<document>", {"filename": filename, "size": len(document.getvalue())}))

    class Message:
        async def reply_text(self, text, **kwargs):
            sent.append((text, kwargs))

    def ask(args):
        sent.clear()
        served["asked"].clear()
        update = SimpleNamespace(message=Message(), effective_chat=SimpleNamespace(id=1), effective_user=SimpleNamespace(id=1))
        real_guard = bot.guard

        async def allowed(update, context):
            return True

        bot.guard = allowed
        try:
            run(bot.buttons_command(update, Context(args)))
        finally:
            bot.guard = real_guard
        return [text for text, _ in sent], list(served["asked"])

    served["rows"] = rows(convertbot={"convgo": 1})
    out, asked = ask([])
    check("no days: seven", asked == [7] and out and out[0].startswith("Button taps, last 7 days"), (asked, out))
    for args, days in ((["1"], 1), (["30"], 30), (["90"], 90), ([" 14 "], 14), (["+3"], 3)):
        out, asked = ask(args)
        check(f"/buttons {args[0]!r}: {days} days", asked == [days], asked)
    for args in (["0"], ["91"], ["-3"], ["abc"], ["7.5"], ["7", "8"], [""], ["1e2"]):
        out, asked = ask(args)
        check(f"/buttons {args!r}: the usage line, and the database is not read",
              out == [bot.BUTTONS_USAGE] and asked == [], (out, asked))
    check("the usage line says the range and the default", "1 to 90" in bot.BUTTONS_USAGE and "7" in bot.BUTTONS_USAGE, bot.BUTTONS_USAGE)

    def broken(days=7):
        raise RuntimeError("the database said no")

    family_link.button_taps_history = broken
    out, _ = ask(["7"])
    check("a database that fails is said, not raised", out and "Couldn't read the button taps" in out[0], out)
    family_link.button_taps_history = history

    long_keys = {f"{'k' * 50}{n:03d}": 1 for n in range(25)}
    served["rows"] = rows(stickerbot=long_keys, convertbot=long_keys, downloaderbot=long_keys, anonbot=long_keys)
    out, _ = ask([])
    check("a report longer than a message goes the way the other long ones do: cut to one message, the whole of it as a file",
          len(out) == 2 and len(out[0]) <= 4096 and out[1] == "<document>" and sent[1][1]["size"] > 4096, [len(t) for t in out])

    source = __import__("inspect").getsource(bot.buttons_command)
    check("the owner's guard is the first thing the command does", source.index("guard(") < source.index("_buttons_report"))
    handlers = (SRC / "bot.py").read_text(encoding="utf-8")
    check("registered as a command, in the menu with a description under 50 characters, and in /help",
          'CommandHandler("buttons", buttons_command)' in handlers
          and any(c.args[0] == "buttons" and len(c.args[1]) < 50 for c in bot.BOT_COMMANDS)
          and "/buttons [days]" in bot.HELP, [c.args for c in bot.BOT_COMMANDS if c.args[0] == "buttons"])


if MODE == "manager":
    manager_checks()
else:
    taps_checks()

import shutil
shutil.rmtree(SCRATCH, ignore_errors=True)
print()
if failures:
    print(f"FAILED ({len(failures)}):")
    for name in failures:
        print("  -", name)
    sys.exit(1)
print("Taps are counted, and the counts say nothing about who." if MODE != "manager" else "/buttons reports the counts, and only to the owner.")
