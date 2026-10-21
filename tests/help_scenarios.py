"""/help in two lengths, and a page for each command behind a button, in all four public bots, driven end to end.

    python tests/help_scenarios.py [sticker_bot convert_bot ...]

The owner, 2026-10-03: "Yes all bots should be similar or share a similar
template." Each bot runs its real main() on tests/sim.py's in-process
Telegram, in a process of its own (all four have modules called bot, db and
i18n), and is asked what a person would ask: /start, /help, the button under
it both ways, /help full. What comes back has to be the bot's own short and
full text, with the same buttons in every bot, and nothing may crash -- a
pattern that does not match or a group that swallows the tap shows up here
and nowhere else.

2.2.0 (the owner, 2026-10-17: each command with options gets a page): /fullhelp
is the full guide in one tap, and /help <name> is the page of one command. And
(2026-10-18, "make it work like the ping command works") the help message
carries a button per page, above the "Full guide" button, and a tap sends the
page as a new message. So the buttons are looked at under /start, /help,
/fullhelp and after the toggle both ways; every button is tapped, and a forged
name is refused; a typed /help_<name> is no page any more; and the same bot is
asked for every page every way it can be asked, in all three languages, and what
comes back has to be the page, in plain text with the link preview off, ending in
/fullhelp. Then the part that makes a page worth reading: every number on it is worked
out when it is shown, so each bot's numbers are changed under it (the file
limit, the ladder, the daily quota...) and the page has to follow.

Needs a Postgres (TEST_DATABASE_URL, or the local default). No network, and
no .env read (ENV_FILES=off): the bots' own files hold real tokens.

To see a check catch breakage, build tests/.mirror (the way tests/run.py does), break the copy of a bot there and
run this again: it runs the bot from the mirror when there is one.
"""
import os
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DSN = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/botfamily_test")
PREFIX = {"sticker_bot": "SBOT", "convert_bot": "CBOT", "downloader_bot": "DBOT", "anon_bot": "ABOT"}

CHILD = r'''
import asyncio, contextlib, copy, inspect, os, re, sys
from pathlib import Path
ROOT = Path(os.environ["HELP_ROOT"])
BOT = os.environ["HELP_BOT"]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, os.getcwd())
sys.path.insert(1, str(ROOT / "shared"))
import sim, db, i18n, shared_features, family_link
import bot as subject

failures, checks = [], 0
def check(label, ok, detail=""):
    global checks
    checks += 1
    print(("  ok   " if ok else "  FAIL ") + label + ("" if ok else f"\n       {detail}"))
    if not ok:
        failures.append(label)

@contextlib.contextmanager
def patched(*changes):
    """patched((module, "NAME", value), ...): the values are in place inside, and back after."""
    old = [(obj, name, getattr(obj, name)) for obj, name, _ in changes]
    try:
        for obj, name, value in changes:
            setattr(obj, name, value)
        yield
    finally:
        for obj, name, value in old:
            setattr(obj, name, value)

def units(text):
    return len(text.encode("utf-16-le")) // 2

def placeholders(text):
    return sorted(set(re.findall(r"\{(\w+)\}", text)))


# The label of each page's button: "/name" -- except DownloaderBot's "download", which is not a command, so
# its button says what the page is about. Written out here, in the three languages, and not read from the bot,
# so that a label the bot forgot to apply shows.
LABELS = {"downloader_bot": {"download": {"en": "\U0001f517 Sending a link", "uz": "\U0001f517 Havola yuborish",
                                          "ru": "\U0001f517 \u041e\u0442\u043f\u0440\u0430\u0432\u043a\u0430 \u0441\u0441\u044b\u043b\u043a\u0438"}}}.get(BOT, {})

def label_of(lang, name):
    return LABELS[name][lang] if name in LABELS else "/" + name

def want_rows(lang, pages):
    """The page buttons under a help message as (label, callback_data): in the order of HELP_PAGES, two to a row."""
    names = list(pages)
    return [[(label_of(lang, n), "helpdet:" + n) for n in names[i:i + 2]] for i in range(0, len(names), 2)]

def rows_of(person, message_id):
    """The buttons of a message in the person's chat, row by row, as (label, callback_data)."""
    markup = (person.sim.tele.find(person.id, message_id) or {}).get("reply_markup") or {}
    return [[(b["text"], b.get("callback_data")) for b in row] for row in markup.get("inline_keyboard", [])]


# What each bot's numbers are changed to, and what has to follow on its pages.
# Called with the pages ({name: function(lang) -> text}); every value is put back after.

def computed_convert(pages, person):
    import convert_utils, formats, ocr_convert
    sf = shared_features
    page = lambda name, lang="en": pages[name](lang)
    rate = family_link.credit_for_stars(1)
    first = sf.DONATE_STAR_OPTIONS[0]

    with patched((convert_utils, "MAX_FILE_MB", 77), (formats, "MAX_BATCH", 33),
                 (ocr_convert, "OCR_MAX_PAGES", 44), (ocr_convert, "OCR_FREE_PAGES", 5),
                 (convert_utils, "FREE_UNDER_MB", 6), (subject, "PENDING_TTL_SECONDS", 1260),
                 (convert_utils, "least_paid_price", lambda: 99)):
        text = page("convert")
        check("the convert page: the file limit is the one in force (convert_utils.MAX_FILE_MB)",
              "Up to 77 MB." in text and "over 77 MB:" in text, text[:300])
        check("... the free limit (convert_utils.FREE_UNDER_MB)",
              "less than 6 MB together" in text and "0–6 MB:" in text and "files under 6 MB" in text, text)
        check("... the batch (formats.MAX_BATCH)", "up to 33 files" in text)
        check("... the pages of a Searchable PDF and of free reading (ocr_convert)",
              "up to 44 scanned pages" in text and "Up to 5 page(s)" in text)
        check("... the minutes, from the pending time-to-live", text.count("21 minutes") == 2, text)
        check("... and the least a paid conversion costs", "99 ⚡ or more" in text)
        check("... in Russian too", "До 77 МБ." in page("convert", "ru") and "33" in page("convert", "ru"))

    # The price list was English in two lines in every language (4.0): now a key each.
    free, over = convert_utils.FREE_UNDER_MB, convert_utils.MAX_FILE_MB
    en = convert_utils.price_tiers_text("en").split("\n")
    check("price_tiers_text in English is what it always was",
          en[0] == f"  0–{free} MB: " + i18n.t("en", "price_free")
          and [l for l in en if l.startswith("  over ")][0].startswith(f"  over {over} MB: "), en)
    for lang, word, unit in (("ru", "больше", "МБ"), ("uz", "dan ortiq", "MB")):
        rows = convert_utils.price_tiers_text(lang).split("\n")
        check(f"price_tiers_text in {lang}: the first and the last tier are in {lang}",
              rows[0] == f"  0–{free} {unit}: " + i18n.t(lang, "price_free")
              and any(word in l and not l.startswith("  over ") for l in rows), rows)

    ladder = family_link.TOPUP_LADDER
    # At today's settings: the ladder, the rule and the example, worked out here from family_link.
    expected = []
    for number, (stars, multiplier) in enumerate(ladder):
        extra = rate * multiplier - rate
        split = f" ({rate} + {extra:g} bonus)" if extra > 0 else ""
        expected.append(f"  • {'your first' if number == 0 else 'the next'} {stars} ⭐: "
                        f"{rate * multiplier:g} ⚡ each{split}")
    expected.append(f"  • after that: {rate} ⚡ each" if ladder else f"  • {rate} ⚡ for every ⭐")
    text = page("recharge")
    check("the recharge page at today's settings: the ladder, as family_link prices it",
          "\n".join(expected) in text, (expected, text))
    quote = family_link.quote_credit(0, first)
    if ladder and quote["bonus"] > 0:
        line = (f"Example: a first payment of {first} ⭐ gives {quote['total']} ⚡: {quote['base']} ⚡ "
                f"that never expire, and {quote['bonus']} ⚡ bonus.")
        check("... the bonus rule, with the days", f"The bonus expires {family_link.BONUS_EXPIRY_DAYS} days after the payment." in text)
        check("... and the example, from quote_credit", line in text, text[-300:])
    check("... the amounts on the buttons", " · ".join(str(n) for n in sf.DONATE_STAR_OPTIONS) + " ⭐)" in text)
    check("... no empty line before a bullet", "\n\n•" not in text)

    with patched((family_link, "TOPUP_LADDER", [(100, 5.0)])):
        text = page("recharge")
        extra = rate * 5 - rate
        check("a ladder of one step (family_link.TOPUP_LADDER = [(100, 5.0)]) shows its 100 and its rate",
              f"your first 100 ⭐: {rate * 5:g} ⚡ each ({rate} + {extra:g} bonus)" in text
              and "the next" not in text and f"after that: {rate} ⚡ each" in text, text)
        quote = family_link.quote_credit(0, first)
        check("... and an example that follows it",
              f"gives {quote['total']} ⚡: {quote['base']} ⚡ that never expire, and {quote['bonus']} ⚡ bonus" in text, text)
        ru = page("recharge", "ru")
        check("... in Russian too", "первые 100 ⭐" in ru and f"по {rate * 5:g} ⚡" in ru, ru)
    # Worked out independently of the page's own arithmetic: a fractional multiplier
    # (review, 2026-10-18) printed 1 per Star while quote_credit paid 1.5.
    with patched((family_link, "TOPUP_MULTIPLIER", 1.5)):
        text = page("recharge")
        step = re.search(r"your first (\d+) ⭐: ([\d.]+) ⚡ each", text)
        check("FAMILY_TOPUP_MULTIPLIER 1.5: the first step's price per Star is what quote_credit pays",
              step is not None and int(step.group(1)) * float(step.group(2))
              == family_link.quote_credit(0, int(step.group(1)))["total"], text)
        check("... and after the ladder, 1.5 ⚡ each", "after that: 1.5 ⚡ each" in text, text)
    with patched((family_link, "TOPUP_LADDER", [(100, 1.0)])):
        text = page("recharge")
        check("a step with no bonus has no split in brackets",
              f"your first 100 ⭐: {rate} ⚡ each\n" in text, text)
    with patched((family_link, "TOPUP_LADDER", [])):
        text = page("recharge")
        check("no ladder: the one flat line", f"  • {rate} ⚡ for every ⭐" in text and "your first" not in text, text)
        check("... no bonus rule", i18n.t("en", "helppage_recharge_bonus_rule", rate=rate, days=family_link.BONUS_EXPIRY_DAYS) not in text
              and "bonus expires" not in text and "never expire" not in text, text)
        check("... the flat example", f"Example: {first} ⭐ give {family_link.quote_credit(0, first)['total']} ⚡." in text, text)
        check("... and no empty line where the rule would have been", "\n\n•" not in text, text)
        check("... the same on the balance page: one topup line, no bonus line",
              f'"+{family_link.quote_credit(0, first)["total"]} ⚡ topup".' in page("balance")
              and '⚡ bonus".' not in page("balance"), page("balance")[-200:])
    with patched((family_link, "BONUS_EXPIRY_DAYS", 45), (sf, "DONATE_STAR_OPTIONS", [20, 60]),
                 (sf, "BALANCE_RECENT_ROWS", 11)):
        text = page("recharge")
        check("the recharge page: the bonus days and the amounts (family_link.BONUS_EXPIRY_DAYS, DONATE_STAR_OPTIONS)",
              "expires 45 days after the payment" in text and "(20 · 60 ⭐)" in text
              and "first payment of 20 ⭐" in text, text)
        text = page("balance")
        check("the balance page: the rows and the days (BALANCE_RECENT_ROWS, BONUS_EXPIRY_DAYS)",
              "your last 11 movements" in text and "expires 45 days after the payment" in text
              and "first payment of 20 ⭐" in text, text)
    text = page("balance")
    quote = family_link.quote_credit(0, first)
    check("the balance page at today's settings: the example is the two rows the ledger writes",
          f'you would see "+{quote["base"]} ⚡ topup" and "+{quote["bonus"]} ⚡ bonus"' in text
          or not ladder or quote["bonus"] == 0, text[-200:])
    check("/balance prints as many rows as the page says (shared_features.BALANCE_RECENT_ROWS)",
          "BALANCE_RECENT_ROWS" in inspect.getsource(sf.balance_command))


COMPUTED = {"convert_bot": computed_convert}


def computed_downloader(pages, person):
    import large_files
    page = lambda name, lang="en": pages[name](lang)

    with patched((db, "DOWNLOADS_PER_DAY", 55), (db, "DOWNLOADS_PER_HOUR", 7), (db, "DONOR_MULTIPLIER", 3.0)):
        text = page("download")
        check("the download page: the quota is the one in force (db.DOWNLOADS_PER_DAY, DOWNLOADS_PER_HOUR)",
              "up to 55 downloads in any 24 hours and 7 in any hour" in text, text)
        check("... and the donors' (DONOR_MULTIPLIER, as db.download_allowance does the sum)",
              "gets 165 a day and 21 an hour" in text, text)
        check("... in Russian too", "55" in page("download", "ru") and "165" in page("download", "ru"))
    with patched((db, "DOWNLOADS_PER_DAY", 0), (db, "DOWNLOADS_PER_HOUR", 0)):
        text = page("download")
        check("no quota (both limits 0): it says it is free and names no number of downloads",
              "It's free." in text and "in any 24 hours" not in text and "an hour, for good" not in text, text)
    with patched((subject, "MAX_PER_USER", 3), (subject, "MAX_PENDING_PER_USER", 9),
                 (subject, "MERGE_MAX_ITEMS", 17), (subject, "MERGE_MAX_MB", 31),
                 (large_files, "FREE_MAX_MB", 33)):
        text = page("download")
        check("the download page: the queue (MAX_PENDING_PER_USER, MAX_PER_USER)",
              "Up to 9 of your links" in text and "I fetch 3 at a time" in text, text)
        check("... the album buttons (MERGE_MAX_ITEMS, MERGE_MAX_MB)", "Up to 17 files and 31 MB." in text, text)
        check("... the free size (large_files.FREE_MAX_MB)", "free up to 33 MB." in text, text)

    with patched((large_files, "CONFIGURED", False)):
        download, settings = page("download"), page("settings")
        check("large files not set up (large_files.CONFIGURED False): no \u26a1 on the download page",
              "\u26a1" not in download and "Anything bigger is refused." in download, download)
        check("... and no large-files bullet on the settings page, one empty line before the example",
              "Large files" not in settings and "\u26a1" not in settings and "instead.\n\nExample:" in settings, settings)
    # The prices are made up here, so a number typed into a page cannot be the right one by luck.
    with patched((large_files, "CONFIGURED", True), (large_files, "LARGE_MAX_MB", 500), (large_files, "FREE_MAX_MB", 33),
                 (large_files, "first_price", lambda: 11), (large_files, "top_price", lambda: 222)):
        first, top = large_files.first_price(), large_files.top_price()
        download, settings = page("download"), page("settings")
        check("large files set up (CONFIGURED True): the prices are in the download page",
              f"\u26a1{first} up to 100 MB" in download and f"(\u26a1{top} for 500 MB)" in download
              and "up to 500 MB" in download and "Anything bigger is refused." not in download, download)
        check("... and in the settings page, the bullet and then an empty line",
              f"\u26a1{first} up to 100 MB, \u26a1{top} for 500 MB" in settings
              and "only.\n\nExample:" in settings and "\n\n\n" not in settings
              and "a download over 33 MB is still fetched, up to 500 MB" in settings
              and "Off: anything over 33 MB is refused" in settings, settings)
        check("... in Russian and Uzbek too", all(f"\u26a1{first}" in page(n, l) for n in ("download", "settings") for l in ("ru", "uz")))
    with patched((subject, "BOT_USERNAME", "somebot")):
        check("the settings page names the bot's own username in the caption", '"via @somebot"' in page("settings"))
    with patched((subject, "BOT_USERNAME", None)):
        check("... and this_bot when it does not know it", '"via @this_bot"' in page("settings"))


COMPUTED["downloader_bot"] = computed_downloader


async def computed_sticker(pages, person):
    import emoji_utils, import_utils, video_sticker
    page = lambda name, lang="en": pages[name](lang)

    with patched((subject, "PACK_TITLE_MAX", 40), (emoji_utils, "DEFAULT_EMOJI", "\U0001f642"),
                 (emoji_utils, "MAX_EMOJI_PER_STICKER", 7), (video_sticker, "STICKER_SIDE", 400),
                 (video_sticker, "MAX_DURATION_S", 5), (video_sticker, "MAX_FPS", 24),
                 (video_sticker, "MAX_BYTES", 300 * 1024), (import_utils, "MAX_IMPORT_PER_RUN", 55)):
        text = page("newpack")
        check("the newpack page: the title limit is the one in force (the bot's PACK_TITLE_MAX)",
              "(up to 40 characters; longer is cut)" in text, text)
        check("... the default emoji and how many may be sent (emoji_utils)",
              "Each sticker gets \U0001f642." in text and "(up to 7)" in text, text)
        check("... the static side (video_sticker.STICKER_SIDE)", "static sticker, 400 px on its longer side" in text, text)
        check("... the video limits (MAX_DURATION_S, MAX_FPS, MAX_BYTES)",
              "video sticker: 400 px, the first 5 seconds, 24 frames a second, no sound, under 300 KB." in text, text)
        check("... in Russian too", all(n in page("newpack", "ru") for n in ("40", "400", "300", "24")))
        check("the addsticker page: the default emoji", "Each gets \U0001f642;" in page("addsticker"))
        check("the import page: how many at a time (import_utils.MAX_IMPORT_PER_RUN)", "Up to 55 each time." in page("import"))
        check("... in Uzbek too", "55" in page("import", "uz"))
    check("the static and the video side are one number today (image_utils and video_sticker)",
          __import__("image_utils").STICKER_SIDE == video_sticker.STICKER_SIDE)

    # The title limit is the one the bot cuts at, not a number typed beside it.
    with patched((subject, "PACK_TITLE_MAX", 10)):
        await person.send("/newpack")
        await person.send("x" * 30)
        check("a title is cut at PACK_TITLE_MAX", person.said('using "' + "x" * 10 + '"'), person.transcript())
    await person.send("/cancel")

    # A page works in the middle of a pack: a tap on a page button answers, sends the page, and the conversation
    # goes on (the handler is outside the conversation, beside the toggle's).
    expected = shared_features.help_page_text("en", pages, "import")
    await person.send("/help")
    help_mid = person.chat[-1]["message_id"]
    await person.send("/newpack")
    before = len(person.chat)
    await person.tap_data("helpdet:import", help_mid)
    check("a page tap answers while the bot waits for the title: one new message, the page",
          [m.get("text") for m in person.chat[before:]] == [expected], [(m.get("text") or "")[:80] for m in person.chat[before:]])
    answers = len(person.sim.tele.answers)
    await person.tap_data("helpdet:nosuchpage", help_mid)
    check("... a forged one gets the alert, and nothing is sent",
          len(person.sim.tele.answers) - answers == 1 and person.sim.tele.answers[-1].get("show_alert") is True
          and person.chat[-1].get("text") == expected, person.sim.tele.answers[answers:])
    await person.send("/fullhelp")
    check("/fullhelp too", person.chat[-1].get("text") == subject.build_help_text("en", full=True))
    await person.send("Mid pack")
    check("... and the title is still taken afterwards: the conversation went on", person.said("Mid pack"), person.transcript())
    before = len(person.chat)
    await person.tap_data("helpdet:newpack", help_mid)
    check("a page tap answers while the pack is being filled, too",
          [m.get("text") for m in person.chat[before:]] == [shared_features.help_page_text("en", pages, "newpack")],
          [(m.get("text") or "")[:80] for m in person.chat[before:]])
    await person.send("/help addsticker")
    check("/help addsticker answers while the pack is being filled",
          person.chat[-1].get("text") == shared_features.help_page_text("en", pages, "addsticker"))
    await person.send("/cancel")

    # The start menu has the page buttons too, and so has the Help button of a start menu sent before 2.1.0.
    await person.send("/start")
    start_mid = person.chat[-1]["message_id"]
    wanted = want_rows("en", pages)
    check("StickerBot's /start menu: the page buttons first, then the toggle, then New pack / My packs",
          rows_of(person, start_mid)[:len(wanted)] == wanted
          and [label for label, _ in rows_of(person, start_mid)[len(wanted)]] == [i18n.t("en", "help_full_button")],
          rows_of(person, start_mid))
    await person.tap_data("menu_help", start_mid)
    check("... an old Help button (menu_help) turns it into the full guide with the page buttons above the toggle",
          (person.sim.tele.find(person.id, start_mid) or {}).get("text") == subject.build_help_text("en", full=True)
          and rows_of(person, start_mid)[:len(wanted)] == wanted
          and rows_of(person, start_mid)[len(wanted)][0][1] == "help:brief", rows_of(person, start_mid))
    await person.tap_data("menu_start", start_mid)
    check("... and menu_start turns it back into the start menu, page buttons and all",
          rows_of(person, start_mid)[:len(wanted)] == wanted and rows_of(person, start_mid)[len(wanted)][0][1] == "help:full",
          rows_of(person, start_mid))


COMPUTED["sticker_bot"] = computed_sticker


async def computed_anon(pages, person):
    import transcript
    page = lambda name, lang="en": pages[name](lang)

    with patched((transcript, "MAX_MESSAGES", 77), (db, "RELAY_RETENTION_DAYS", 33)):
        text = page("export")
        check("the export page: the most messages one export holds (transcript.MAX_MESSAGES)",
              "Up to 77 messages each time." in text, text)
        check("... and how long a conversation is remembered (db.RELAY_RETENTION_DAYS)",
              "forgotten 33 days after it last moved" in text, text)
        check("... in Russian and Uzbek too", all("77" in page("export", l) and "33" in page("export", l) for l in ("ru", "uz")))

    # The example on the which page is the bot's own answer, not a copy of it.
    real = i18n.STRINGS["en"]["which_owner"]
    i18n.STRINGS["en"]["which_owner"] = "Made up: #{conv_number}."
    try:
        check("the which page: the example is what /which answers (i18n key which_owner)",
              'I answer: "Made up: #4."' in page("which"), page("which"))
    finally:
        i18n.STRINGS["en"]["which_owner"] = real
    for lang in ("en", "uz", "ru"):
        check(f"... {lang}: the real answer, for conversation 4", i18n.t(lang, "which_owner", conv_number=4) in page("which", lang))
    check("the pages use the document's own words for the two sides, in every language",
          all(i18n.t(l, "export_doc_owner") in page("export", l) and i18n.t(l, "export_doc_anon") in page("export", l)
              for l in ("en", "uz", "ru")))
    check("... and the buttons the bot really has, in every language",
          all(i18n.t(l, key).split(" \u00b7")[0] in page("export", l)
              for l in ("en", "uz", "ru") for key in ("export_button_copy", "export_button_doc", "export_pick_all"))
          and all(i18n.t(l, key) in page("link", l) for l in ("en", "uz", "ru")
                  for key in ("link_button_pause", "link_button_newlink")), page("export", "uz"))

    # Somebody who was here before reactions travelled is owed a one-time note, after whatever they do next;
    # the full guide lists every reaction, so reading it -- either way -- is being told. A page is not.
    note = i18n.t("en", "reactions_update_note")
    user_data = person.sim.app.user_data[person.id]

    async def owed_note_after(action):
        """What was done is a typed command, or a tap: (callback_data, message id)."""
        with db.pooled() as conn:
            conn.execute("UPDATE user_settings SET knows_reactions = NULL WHERE user_id = %s", (person.id,))
            conn.commit()
        user_data.pop(subject.KNOWS_REACTIONS_KEY, None)
        before = sum(note in text for text in person.texts())
        if isinstance(action, tuple):
            await person.tap_data(*action)
        else:
            await person.send(action)
        return sum(note in text for text in person.texts()) - before

    await person.send("/help")
    help_mid = person.chat[-1]["message_id"]
    check("a page is not the full guide: whoever is owed the reactions note still gets it, from /help link",
          await owed_note_after("/help link") == 1)
    check("... and from a tap on a page button, which does not mark them as told",
          await owed_note_after(("helpdet:link", help_mid)) == 1)
    check("/help full tells them about reactions, so no note", await owed_note_after("/help full") == 0)
    check("/fullhelp does the same", await owed_note_after("/fullhelp") == 0)


COMPUTED["anon_bot"] = computed_anon


async def main():
    db.init_db()
    # A brand-new person each run: their language row gone, whichever table
    # this bot keeps it in (DownloaderBot's is "settings").
    with db.pooled() as conn:
        table = conn.execute("SELECT CASE WHEN to_regclass('user_settings') IS NULL "
                             "THEN 'settings' ELSE 'user_settings' END").fetchone()[0]
        conn.execute(f"DELETE FROM {table} WHERE user_id = 7001")
        conn.commit()
    tele = sim.FakeTelegram(bot_username="helptest_bot")
    # Every call the bot makes to Telegram, as it was made: the sim keeps what
    # arrived, not how it was asked for (the parse mode, the link preview).
    sent = []
    real_api = tele.api
    async def spying_api(endpoint, data):
        sent.append((endpoint, dict(data)))
        return await real_api(endpoint, data)
    tele.api = spying_api
    s, _ = await sim.build(subject, tele)
    async def record(update, context):
        crashes.append(f"{type(context.error).__name__}: {context.error}")
    s.app.add_error_handler(record)
    person = s.person(7001, "Person")
    await person.send("/start")
    await person.tap("English")
    brief, full = subject.build_help_text("en"), subject.build_help_text("en", full=True)
    check("picking a language ends at the short help",
          any(brief in (m.get("text") or "") for m in person.chat), person.transcript())

    sf = shared_features
    pages, aliases = subject.HELP_PAGES, subject.HELP_ALIASES
    names = list(pages)
    toggle_label = lambda lang, is_full: i18n.t(lang, "help_brief_button" if is_full else "help_full_button")

    def buttons_are_right(label, message_id, lang, is_full):
        """The page buttons are the first rows (in order, two to a row, each with its label), the toggle is the
        row right under them, and every helpdet: callback is at most 64 bytes and fits the handler's pattern."""
        rows, wanted = rows_of(person, message_id), want_rows(lang, pages)
        check(f"{label}: the page buttons come first, in the order of HELP_PAGES, two to a row, each with its label",
              rows[:len(wanted)] == wanted, rows)
        data = "help:brief" if is_full else "help:full"
        check(f"{label}: ... and the toggle is the row right under them",
              len(rows) > len(wanted) and rows[len(wanted)][:1] == [(toggle_label(lang, is_full), data)], rows)
        callbacks = [d for row in rows for _, d in row if d and d.startswith("helpdet:")]
        check(f"{label}: ... every helpdet: callback is at most 64 bytes and matches HELP_PAGE_PATTERN",
              len(callbacks) == len(names)
              and all(len(d.encode("utf-8")) <= 64 and re.fullmatch(sf.HELP_PAGE_PATTERN, d) for d in callbacks), callbacks)

    async def keyboards(lang):
        """/start, /help, the toggle both ways, /help full and /fullhelp: the page buttons are there every time,
        above the toggle, and the text is the bot's own. Returns the id of the /help message (short again)."""
        brief_text, full_text = subject.build_help_text(lang), subject.build_help_text(lang, full=True)
        await person.send("/start")
        start = person.chat[-1]
        check(f"{lang} /start shows the short help", brief_text in (start.get("text") or ""), (start.get("text") or "")[:200])
        buttons_are_right(f"{lang} /start", start["message_id"], lang, False)
        await person.send("/help")
        message = person.chat[-1]
        mid = message["message_id"]
        check(f"{lang} /help is the short help", message.get("text") == brief_text, (message.get("text") or "")[:200])
        buttons_are_right(f"{lang} /help", mid, lang, False)
        await person.tap(toggle_label(lang, False), mid)
        check(f"{lang}: the toggle turns it into the full guide, in place",
              (tele.find(person.id, mid) or {}).get("text") == full_text, ((tele.find(person.id, mid) or {}).get("text") or "")[:200])
        buttons_are_right(f"{lang} /help after the toggle to the full guide", mid, lang, True)
        await person.tap(toggle_label(lang, True), mid)
        check(f"{lang}: ... and back", (tele.find(person.id, mid) or {}).get("text") == brief_text)
        buttons_are_right(f"{lang} /help after the toggle back", mid, lang, False)
        await person.send("/help full")
        check(f"{lang} /help full goes straight to the full guide", person.chat[-1].get("text") == full_text,
              (person.chat[-1].get("text") or "")[:200])
        buttons_are_right(f"{lang} /help full", person.chat[-1]["message_id"], lang, True)
        await person.send("/fullhelp")
        reply = person.chat[-1]
        check(f"{lang} /fullhelp gives exactly the full guide", reply.get("text") == full_text, (reply.get("text") or "")[:200])
        buttons_are_right(f"{lang} /fullhelp", reply["message_id"], lang, True)
        await person.tap(toggle_label(lang, True), reply["message_id"])
        check(f"{lang} /fullhelp: ... whose toggle goes back to the short one, in place",
              (tele.find(person.id, reply["message_id"]) or {}).get("text") == brief_text)
        buttons_are_right(f"{lang} /fullhelp after the toggle back", reply["message_id"], lang, False)
        return mid

    async def taps(lang, mid):
        """A tap on each page button of the help message `mid`: exactly one new message, the page, plain text,
        previews off, no keyboard -- and the help message itself, and the chat before it, are as they were."""
        keep = copy.deepcopy(tele.find(person.id, mid))
        for name in names:
            before, calls, answers = len(person.chat), len(sent), len(tele.answers)
            await person.tap_data("helpdet:" + name, mid)
            new, made = person.chat[before:], sent[calls:]
            expected = sf.help_page_text(lang, pages, name)
            check(f"{lang} tap {name}: exactly one new message, and it is the page",
                  len(new) == 1 and new[0].get("text") == expected, [(m.get("text") or "")[:80] for m in new])
            wire = [data for endpoint, data in made if endpoint == "sendMessage"]
            preview = (wire[-1].get("link_preview_options") if wire else None)
            preview = preview.to_dict() if hasattr(preview, "to_dict") else (preview or {})
            check(f"{lang} tap {name}: ... as plain text with the link preview off, and no keyboard",
                  len(wire) == 1 and not wire[0].get("parse_mode") and preview.get("is_disabled") is True
                  and not wire[0].get("reply_markup") and not person.buttons(new[0]["message_id"]) if new else False,
                  (wire, preview))
            check(f"{lang} tap {name}: ... answered once, without an alert, and nothing was edited",
                  len(tele.answers) - answers == 1 and not tele.answers[-1].get("show_alert") and not tele.answers[-1].get("text")
                  and not [endpoint for endpoint, _ in made if endpoint.startswith("edit")], (tele.answers[answers:], made))
            check(f"{lang} tap {name}: ... and the help message is exactly as it was", tele.find(person.id, mid) == keep)
        gone = i18n.t(lang, "help_page_gone")
        for forged in ["nosuchpage"] + list(aliases):
            before, calls, answers = len(person.chat), len(sent), len(tele.answers)
            await person.tap_data("helpdet:" + forged, mid)
            made = sent[calls:]
            check(f"{lang} forged tap helpdet:{forged}: the 'no longer exists' alert, and nothing is sent or edited",
                  len(person.chat) == before and len(tele.answers) - answers == 1
                  and tele.answers[-1].get("text") == gone and tele.answers[-1].get("show_alert") is True
                  and not [endpoint for endpoint, _ in made if endpoint == "sendMessage" or endpoint.startswith("edit")],
                  (tele.answers[answers:], made))
        before, calls = len(person.chat), len(sent)
        await person.tap_data("helpdet:NOSUCH", mid)
        check(f"{lang} a tap whose name does not fit the pattern is taken by no handler: nothing happens",
              len(person.chat) == before and not [e for e, _ in sent[calls:] if e == "sendMessage" or e.startswith("edit")])
        check(f"{lang} ... and the help message is still as it was", tele.find(person.id, mid) == keep)

    # ---- /start, /help, /fullhelp and the toggle: the buttons, then a tap on each ----
    help_mid = await keyboards("en")
    check("the short help points at /feedback, and says it is one-way",
          "/feedback" in brief and "can't reply" in brief)
    await taps("en", help_mid)

    # ---- no links line any more: both versions end with the sibling blurb, or with their own text ----
    problems = [f"{lang} {label}" for lang in ("en", "uz", "ru") for label, text in (
        ("short help", subject.build_help_text(lang)), ("full guide", subject.build_help_text(lang, full=True)))
        if "In detail" in text or "Batafsil:" in text or "Подробно:" in text or "/help_" in text]
    check("the short help and the full guide hold no 'In detail' line and no /help_<x> link, in en, uz and ru",
          not problems, problems)
    check("shared_features no longer has the links line or the /help_<name> handler factory",
          not hasattr(sf, "help_pages_line") and not hasattr(sf, "help_page_command"))
    real_blurb = subject.sibling_bots_blurb
    subject.sibling_bots_blurb = lambda name, lang: "BLURB"
    try:
        for label, text in (("short help", subject.build_help_text("en")), ("full guide", subject.build_help_text("en", full=True))):
            check(f"the {label} ends with the sibling blurb after one empty line",
                  text.endswith("\n\nBLURB") and not text.endswith("\n\n\nBLURB"), text[-100:])
    finally:
        subject.sibling_bots_blurb = real_blurb

    # ---- a page, asked for every way (/help <name>) ----
    everything = i18n.t("en", "help_page_everything")
    for name in names:
        expected = sf.help_page_text("en", pages, name)
        loose = []
        for command in (f"/help {name}", f"/help help_{name}", f"/help /help_{name}", f"/help /{name}", f"/help {name.upper()}",
                        f"/help {name}@helptest_bot", f"/help@helptest_bot {name}"):
            await person.send(command)
            reply = person.chat[-1]
            check(f"{command} gives the {name} page", reply.get("text") == expected, (reply.get("text") or "")[:200])
            check(f"{command}: with no buttons", not person.buttons(reply["message_id"]), person.buttons(reply["message_id"]))
            wire = [data for endpoint, data in sent if endpoint == "sendMessage"][-1]
            preview = wire.get("link_preview_options")
            preview = preview.to_dict() if hasattr(preview, "to_dict") else (preview or {})
            if wire.get("parse_mode") or preview.get("is_disabled") is not True:
                loose.append((command, wire.get("parse_mode"), preview))
        check(f"the {name} page goes out as plain text, with the link preview off, however it was asked for",
              not loose, loose)
        rows = expected.split("\n")
        check(f"the {name} page ends with /fullhelp and nothing about other pages",
              rows[-1] == everything and "/help_" not in expected and rows[-2] == "", rows[-3:])
    for alias, target in aliases.items():
        expected = sf.help_page_text("en", pages, target)
        for command in (f"/help {alias}", f"/help help_{alias}"):
            await person.send(command)
            check(f"{command} gives the {target} page", person.chat[-1].get("text") == expected,
                  (person.chat[-1].get("text") or "")[:200])
    await person.send("/help nonsense")
    check("/help nonsense gives the short help", person.chat[-1].get("text") == brief, (person.chat[-1].get("text") or "")[:200])

    # ---- a typed /help_<name> is not a page any more: it gets what any unknown command gets ----
    async def answer_to(command):
        before = len(person.chat)
        await person.send(command)
        # [before] is what the person typed; what follows is the bot's answer, if any.
        return [(m.get("text"), bool(m.get("reply_markup"))) for m in person.chat[before + 1:]]

    baseline = await answer_to("/zzznosuchcommand")
    page_texts = [sf.help_page_text("en", pages, n) for n in names]
    for typed in [f"/help_{n}" for n in names] + [f"/help_{a}" for a in aliases] + ["/help_nonsense"]:
        got = await answer_to(typed)
        check(f"{typed} is no longer a page: the bot answers it as it answers any unknown command",
              got == baseline and not [t for t, _ in got if t in page_texts], (got, baseline))
        got = await answer_to(typed + "@helptest_bot")
        check(f"{typed}@helptest_bot is no page either", not [t for t, _ in got if t in page_texts], got)

    # ---- what a page is made of, in every language ----
    problems = []
    for lang in ("en", "uz", "ru"):
        texts = {f"{lang} {name} page": sf.help_page_text(lang, pages, name) for name in names}
        texts[f"{lang} short help"] = subject.build_help_text(lang)
        texts[f"{lang} full guide"] = subject.build_help_text(lang, full=True)
        for label, text in texts.items():
            if not text.strip():
                problems.append(f"{label}: empty")
            if re.search(r"[{}]", text):
                problems.append(f"{label}: a brace is left")
            if "\n\n\n" in text:
                problems.append(f"{label}: two empty lines in a row")
            if units(text) > 4096:
                problems.append(f"{label}: {units(text)} UTF-16 units, over Telegram's 4096")
    check("every page, the short help and the full guide, in en, uz and ru: not empty, no brace left, no double gap, one message",
          not problems, problems)
    keys = lambda lang: {k for k in i18n.STRINGS[lang] if k.startswith(("helppage_", "help_page", "help_btn", "price_free_upto", "price_over"))}
    check("the pages' keys are the same in en, uz and ru", keys("en") == keys("uz") == keys("ru"),
          (keys("en") ^ keys("uz"), keys("en") ^ keys("ru")))
    check("... help_page_gone is one of them, and the links line's two keys are gone from every language",
          all("help_page_gone" in i18n.STRINGS[l] and "help_pages_line" not in i18n.STRINGS[l]
              and "help_page_others" not in i18n.STRINGS[l] for l in ("en", "uz", "ru")))
    drift = [f"{key} ({lang}): {placeholders(i18n.STRINGS[lang][key])} instead of {placeholders(i18n.STRINGS['en'][key])}"
             for key in keys("en") for lang in ("uz", "ru")
             if placeholders(i18n.STRINGS[lang].get(key, "")) != placeholders(i18n.STRINGS["en"][key])]
    check("... and each takes the same values in every language", not drift, drift)

    # ---- the numbers on the pages are worked out when the page is shown ----
    if BOT in COMPUTED:
        result = COMPUTED[BOT](pages, person)
        if inspect.isawaitable(result):
            await result

    # ---- in the other languages, through the bot: the pages, the buttons and their labels, a tap on each ----
    for code, command in (("uz", "/uz"), ("ru", "/rus")):
        await person.send(command)
        wrong = []
        for name in names + list(aliases):
            await person.send(f"/help {name}")
            target = aliases.get(name, name)
            if person.chat[-1].get("text") != sf.help_page_text(code, pages, target) \
                    or person.chat[-1].get("text") == sf.help_page_text("en", pages, target):
                wrong.append(name)
        check(f"after {command}, /help <name> gives every page and alias in {code}", not wrong, wrong)
        await person.send("/fullhelp")
        check(f"after {command}, /fullhelp is in {code}", person.chat[-1].get("text") == subject.build_help_text(code, full=True))
        mid = await keyboards(code)
        await taps(code, mid)

    check("nothing crashed", not crashes, " | ".join(crashes))
    print(f"{checks} checks, FAILURES: {len(failures)}")
    db.close_pool()
    return 1 if failures else 0

crashes = []
raise SystemExit(asyncio.run(main()))
'''


def main() -> int:
    bots = sys.argv[1:] or list(PREFIX)
    failed = []
    for bot in bots:
        print(f"\n== {bot} ==", flush=True)
        prefix = PREFIX[bot]
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1", ENV_FILES="off",
                   HELP_ROOT=str(ROOT), HELP_BOT=bot, DATABASE_URL=DSN, DB_SCHEMA=f"{bot}_helpsim",
                   DEPLOY_SAFETY="off", FLOOD_UPDATES_PER_MINUTE="0", SIBLING_BOTS="",
                   FAMILY_BUS="off", **{f"{prefix}_TOKEN": "1:AA", f"{prefix}_USERNAME": "helptest_bot",
                                        f"{prefix}_ADMIN_ID": "1"})
        # The bot as tests/run.py mirrors it, when there is a mirror (to run a broken copy); else the source.
        mirrored = ROOT / "tests" / ".mirror" / bot
        # The program goes in on stdin: as an argument ("-c") it outgrew Windows' 32,767 characters for a command line.
        result = subprocess.run([sys.executable, "-"], input=CHILD.encode("utf-8"),
                                cwd=mirrored if mirrored.exists() else ROOT / bot, env=env)
        if result.returncode:
            failed.append(bot)
    print("\nFAILED: " + ", ".join(failed) if failed else "\nevery bot's help is the same template")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
