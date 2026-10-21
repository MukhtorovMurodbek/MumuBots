"""Text recognition, wired into the bot: what is offered, what it costs, what runs.

    python3 tests/test_ocr_wiring.py

test_ocr.py checks the module that reads the text (ocr_convert.py). This file
checks everything around it:

  what the format menu offers, and when -- the targets, the language row, the
    30-page note, a PDF whose pages could not be counted, a host with no
    language data;
  what it costs -- one page under 3 MB is free, two are priced, the time limit
    is one function, and the quote and the queue take their numbers from it;
  what the worker is told -- the language and the number of pages the price
    counted -- and that it reads nothing it was not sold;
  that the pages are counted in a process of their own (a PDF is a stranger's
    file and MuPDF parses it in C);
  that the searchable PDF is a `.pdf`, never overwrites the file it came from,
    and is never called "ocrpdf" to the person;
  the texts, in all three languages.

The bot's handlers are driven with the same small fakes test_batching.py uses;
the PDFs and pictures are drawn here at run time. The checks that need the
engine and the language data (TESSDATA_PREFIX, or the laptop's TempTools copy)
say SKIP without them and the file still passes.
"""
import ast
import asyncio
import contextlib
import json
import os
import pathlib
import shutil
import sys
import tempfile
import time
from types import SimpleNamespace

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / ".mirror" / "convert_bot") if (HERE / ".mirror").exists()
                else str(HERE.parent / "convert_bot"))
sys.path.insert(1, str(HERE.parent / "shared"))
sys.path.append(str(HERE / "stubs"))             # after site-packages: the real Pillow beats the stand-in
# Files over 20 MB are test_big_files.py's; here the bot runs without them.
for _variable in ("CBOT_API_ID", "CBOT_API_HASH", "CONVERT_MAX_FILE_MB", "LOCAL_BOT_API_URL"):
    os.environ.pop(_variable, None)
os.environ.setdefault("CBOT_TOKEN", "test")
os.environ.setdefault("CBOT_USERNAME", "test")
os.environ.setdefault("DATABASE_URL", "postgresql://stub")
os.environ["ENV_FILES"] = "off"
_LAPTOP_TESSDATA = pathlib.Path(r"C:\Bitura\.Projects\MumuBots\TempTools\tessdata")
if not os.environ.get("TESSDATA_PREFIX") and _LAPTOP_TESSDATA.is_dir():
    os.environ["TESSDATA_PREFIX"] = str(_LAPTOP_TESSDATA)
STAGE = tempfile.mkdtemp(prefix="convertbot-ocrwire-")
os.environ["CONVERT_UPLOAD_DIR"] = STAGE

import bot  # noqa: E402
import convert_runner  # noqa: E402
import convert_utils as cu  # noqa: E402
import formats  # noqa: E402
import i18n  # noqa: E402
import jobs  # noqa: E402
import ocr_convert as oc  # noqa: E402
import pricing  # noqa: E402

failures = []
WORK = pathlib.Path(tempfile.mkdtemp(prefix="ocrwire-work-"))

try:
    import pymupdf
except ImportError:
    pymupdf = None
HAVE_ENGINE = pymupdf is not None and hasattr(pymupdf.Pixmap, "pdfocr_tobytes")
CAN_READ = HAVE_ENGINE and oc.tessdata_dir() is not None and formats.met("ocr")


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL  ") + name + (f" -- {detail}" if not ok and detail else ""))
    if not ok:
        failures.append(name)


def skip(name, why):
    print(f"  SKIP  {name} -- {why}")


# ---------------------------------------------------------------------------
# Inputs, drawn here
# ---------------------------------------------------------------------------

def text_png(text, size=26):
    """A line of text as PNG bytes, black on white."""
    doc = pymupdf.open()
    page = doc.new_page(width=420, height=120)
    writer = pymupdf.TextWriter(page.rect)
    writer.append((20, 70), text, font=pymupdf.Font("helv"), fontsize=size)
    writer.write_text(page)
    data = page.get_pixmap(dpi=150).tobytes("png")
    doc.close()
    return data


def paper_png():
    """A picture of blank paper, as PNG bytes."""
    doc = pymupdf.open()
    data = doc.new_page(width=60, height=40).get_pixmap(dpi=72).tobytes("png")
    doc.close()
    return data


def make_pdf(path, pages):
    """`pages` is a list of ("text", words), ("scan", words), ("paper",) -- a
    picture of blank paper, a scanned page with nothing on it -- or ("blank",),
    a page with nothing on it at all, which is not a scan (there is no picture)."""
    doc = pymupdf.open()
    for kind, *rest in pages:
        page = doc.new_page(width=420, height=120)
        if kind == "text":
            page.insert_text((30, 70), rest[0], fontsize=14)
        elif kind == "scan":
            page.insert_image(page.rect, stream=text_png(rest[0]))
        elif kind == "paper":
            page.insert_image(page.rect, stream=paper_png())
    doc.save(str(path))
    doc.close()
    return pathlib.Path(path)


def glyph_bomb(path, glyphs=2_000_000):
    """A few kilobytes that take MuPDF minutes to lay out: one page with a picture on
    it, so that it is looked at, and a content stream of `glyphs` glyphs (a million
    took 17 s on the laptop, four million over two minutes)."""
    doc = pymupdf.open()
    page = doc.new_page(width=420, height=260)
    page.insert_image(page.rect, stream=paper_png())
    page.insert_text((10, 10), "x", fontsize=1)             # puts a font in the page's resources
    font = page.get_fonts()[0][4]
    doc.update_stream(page.get_contents()[-1], f"BT /{font} 1 Tf 10 10 Td ({'x' * glyphs}) Tj ET".encode())
    doc.save(str(path), deflate=True)
    doc.close()
    return pathlib.Path(path)


EN = "Invoice number four hundred"
RU = "Проверка текста"
TEXT_PAGE = "Quarterly report: revenue grew by twelve percent."


# ---------------------------------------------------------------------------
# The smallest Telegram these handlers can tell from the real one
# ---------------------------------------------------------------------------

class FakeMessage:
    def __init__(self, chat_id=7):
        self.chat_id = chat_id
        self.message_id = 1
        self.replies = []
        self.keyboards = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)
        self.keyboards.append(kwargs.get("reply_markup"))
        return FakeMessage(self.chat_id)


class FakeQuery:
    def __init__(self, data, message=None):
        self.data = data
        self.message = message or FakeMessage()
        self.answers = []

    async def answer(self, text=None, show_alert=False, **kwargs):
        self.answers.append((text, show_alert))


USER = SimpleNamespace(id=4242, username="tester")


def new_context(lang="en", **user_data):
    data = {"lang": lang}
    data.update(user_data)
    return SimpleNamespace(bot=SimpleNamespace(), user_data=data, job_queue=None, job=None,
                           application=SimpleNamespace(name="app"))


def update_for(query):
    return SimpleNamespace(callback_query=query, effective_user=USER, message=None)


def buttons(markup):
    """[[(label, callback_data), ...], ...] of a keyboard."""
    # python-telegram-bot's own buttons where it is installed, tests/stubs' stand-ins where not.
    return [[(b.text if hasattr(b, "text") else b.args[0],
              b.callback_data if hasattr(b, "callback_data") else b.kwargs.get("callback_data")) for b in row]
             for row in markup.inline_keyboard]


def flat(markup):
    return [pair for row in buttons(markup) for pair in row]


def data_of(markup):
    return [data for _, data in flat(markup)]


EDITS = []


async def fake_edit(message, bot_, text, **kwargs):
    EDITS.append((text, kwargs.get("reply_markup")))


bot.edit_in_place = fake_edit


def latest_menu(message):
    return message.replies[-1], message.keyboards[-1]


async def open_menu(path, ext, lang="en", **user_data):
    """The upload handler's last step, for a file already on disk."""
    message, context = FakeMessage(), new_context(lang, **user_data)
    await bot._present_conversion_options(message, context, USER, [pathlib.Path(path)], ext)
    return message, context


@contextlib.contextmanager
def ocr_unmet():
    """The host without the language data: the "ocr" requirement not met."""
    formats.probe()
    saved = (set(formats._MET), formats._PROBED)
    try:
        formats._MET = set(formats._MET) - {"ocr"}
        formats._PROBED = True
        yield
    finally:
        formats._MET, formats._PROBED = saved


@contextlib.contextmanager
def recording_workers():
    """Note every payload a child is started with, and start it as usual."""
    seen = []
    real = jobs._start_worker

    async def spy(payload):
        seen.append(dict(payload))
        return await real(payload)
    jobs._start_worker = spy
    try:
        yield seen
    finally:
        jobs._start_worker = real


def read_text(path):
    return pathlib.Path(path).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. What the tables offer
# ---------------------------------------------------------------------------

def offered():
    print("=== what is offered ===")
    formats.probe()
    declared_png = formats.declared_targets("png")
    check("a picture's TXT and searchable PDF are in the More list, not on the first screen",
          "txt" in declared_png and "ocrpdf" in declared_png
          and "txt" not in declared_png[:formats.POPULAR_COUNT] and "ocrpdf" not in declared_png[:formats.POPULAR_COUNT],
          str(declared_png))
    declared_pdf = formats.declared_targets("pdf")
    check("a PDF's searchable PDF comes right after TXT",
          declared_pdf.index("ocrpdf") == declared_pdf.index("txt") + 1, str(declared_pdf))
    check("the longest callback data fits in Telegram's 64 bytes",
          len("convocr".encode()) <= 64 and len("convfmt:ocrpdf".encode()) <= 64)
    check("ocrpdf survives the bot's extension filter (8 characters, [a-z0-9])",
          bot._safe_ext("ocrpdf") == "ocrpdf" and len("ocrpdf") <= 8)
    check("the searchable PDF is only ever a target", "ocrpdf" not in formats.CATEGORY_OF)

    with ocr_unmet():
        check("without the language data a picture is not offered TXT or a searchable PDF",
              "txt" not in formats.targets_for("png") and "ocrpdf" not in formats.targets_for("png"),
              str(formats.targets_for("png")))
        check("... and a PDF is not offered the searchable PDF, but keeps TXT",
              "ocrpdf" not in formats.targets_for("pdf") and "txt" in formats.targets_for("pdf"),
              str(formats.targets_for("pdf")))
        check("... and a subtitle file keeps its own TXT", "txt" in formats.targets_for("srt"))

    # The real thing: language data that is not there, probed for real.
    holder = tempfile.mkdtemp(prefix="ocrwire-empty-")
    saved_env = os.environ.get("TESSDATA_PREFIX")
    try:
        os.environ["TESSDATA_PREFIX"] = holder
        formats._PROBED = False
        formats.probe()
        check("probe() does not meet \"ocr\" when TESSDATA_PREFIX holds no language data", not formats.met("ocr"))
        check("... so nothing that reads text is offered",
              "ocrpdf" not in formats.targets_for("pdf") and "txt" not in formats.targets_for("png"))
    finally:
        if saved_env is None:
            os.environ.pop("TESSDATA_PREFIX", None)
        else:
            os.environ["TESSDATA_PREFIX"] = saved_env
        formats._PROBED = False
        formats.probe()
        shutil.rmtree(holder, ignore_errors=True)
    if CAN_READ:
        check("with the language data, \"ocr\" is met and a picture is offered both",
              "txt" in formats.targets_for("png") and "ocrpdf" in formats.targets_for("png"))
        check("... and the first screen of a PDF is DOCX, PNG, JPG, TXT, Searchable PDF, HTML",
              formats.popular_targets("pdf") == ["docx", "png", "jpg", "txt", "ocrpdf", "html"],
              str(formats.popular_targets("pdf")))
    else:
        skip("the offers with the language data", "no engine or language data here")


# ---------------------------------------------------------------------------
# 2. Which conversions read text, and what they cost
# ---------------------------------------------------------------------------

def pricing_and_limits():
    print("\n=== what it costs ===")
    pages = cu.ocr_job_pages
    check("a picture to TXT or a searchable PDF is one page",
          pages("png", "txt") == 1 and pages("jpg", "ocrpdf") == 1 and pages("webp", "txt", 0) == 1)
    check("a PDF reads the pages it was priced for, to either",
          pages("pdf", "txt", 7) == 7 and pages("pdf", "ocrpdf", 7) == 7)
    check("a PDF with none counted reads none: today's TXT, and no searchable PDF",
          pages("pdf", "txt", 0) == 0 and pages("pdf", "ocrpdf", 0) == 0)
    check(f"exactly {oc.OCR_MAX_PAGES} are read, and more than that none: TXT is then today's (no page is read)",
          pages("pdf", "txt", oc.OCR_MAX_PAGES) == oc.OCR_MAX_PAGES and pages("pdf", "txt", oc.OCR_MAX_PAGES + 1) == 0
          and pages("pdf", "ocrpdf", oc.OCR_MAX_PAGES + 1) == 0 and pages("pdf", "txt", 500) == 0)
    check("an album reads no page: more than one file is none, whatever was counted",
          pages("pdf", "txt", 7, 2) == 0 and pages("pdf", "ocrpdf", 7, 2) == 0 and pages("png", "txt", 0, 2) == 0
          and pages("png", "ocrpdf", 1, 10) == 0 and pages("pdf", "txt", 7, 1) == 7 and pages("png", "txt", 0, 1) == 1)
    check("nothing else reads text",
          pages("pdf", "png", 5) == 0 and pages("srt", "txt") == 0 and pages("docx", "txt", 3) == 0
          and pages("dng", "txt") == 0 and pages("svg", "txt") == 0 and pages("mp4", "txt") == 0
          and pages("png", "jpg") == 0)

    free_bytes, over_bytes = 1_000_000, 4_000_000
    check("one page under 3 MB is free, to TXT and to a searchable PDF",
          cu.price_for_job("png", "txt", free_bytes)[0] == 0 and cu.price_for_job("png", "ocrpdf", free_bytes)[0] == 0
          and cu.price_for_job("pdf", "txt", 200_000, 1, 1)[0] == 0
          and cu.price_for_job("pdf", "ocrpdf", 200_000, 1, 1)[0] == 0)
    check("two pages are priced, though the file is tiny",
          cu.price_for_job("pdf", "txt", 200_000, 2, 2)[0] > 0 and cu.price_for_job("pdf", "ocrpdf", 200_000, 2, 2)[0] > 0)
    check("... and so is a picture over 3 MB, as any conversion is",
          cu.price_for_job("png", "txt", over_bytes)[0] > 0 and cu.price_for_job("png", "ocrpdf", over_bytes)[0] > 0)

    # The time limit is one function, with the spec's formula.
    check("a free job gets the free time limit", jobs.ocr_time_limit(1, False) == jobs.FREE_TIME_LIMIT_S)
    for count in (1, 2, 5, 30, 1000):
        want = int(min(jobs.TIME_LIMIT_S, jobs.TIME_BASE_S + count * oc.OCR_PAGE_S))
        check(f"a paid job reading {count} page(s) may run {want} s", jobs.ocr_time_limit(count, True) == want,
              str(jobs.ocr_time_limit(count, True)))
    if (oc.OCR_PAGE_S, jobs.TIME_BASE_S, jobs.TIME_LIMIT_S) == (15, 120, 600):
        check("at the default settings two pages are 150 s, thirty are 570 s and the ceiling is 600 s",
              (jobs.ocr_time_limit(2, True), jobs.ocr_time_limit(30, True), jobs.ocr_time_limit(60, True)) == (150, 570, 600))

    # ... and the file's own part is in it: what any conversion of that file is
    # given, with the pages' time added to it, up to the paid ceiling.
    mb = 1024 * 1024
    check("a free job is the free limit whatever the file", jobs.ocr_time_limit(1, False, 90 * mb, 2.0) == jobs.FREE_TIME_LIMIT_S)
    for billable, work, count in ((0, 1.0, 1), (3 * mb, 1.0, 2), (90 * mb, 1.0, 1), (90 * mb, 1.4, 5), (400 * mb, 1.0, 30)):
        want = int(min(jobs.TIME_LIMIT_S, jobs.time_limit_for(billable, work, True) + count * oc.OCR_PAGE_S))
        got = jobs.ocr_time_limit(count, True, billable, work)
        check(f"{count} page(s) of a {billable // mb} MB job at work {work}: the file's limit plus the pages', {want} s",
              got == want, str(got))
    size_part = jobs.time_limit_for(90 * mb, 1.0, True) - jobs.time_limit_for(0, 1.0, True)
    check("... the file's part is more than nothing for 90 MB, and is added to the pages'",
          size_part > 0 and jobs.ocr_time_limit(1, True, 90 * mb, 1.0)
          == min(jobs.TIME_LIMIT_S, jobs.ocr_time_limit(1, True) + size_part), str(size_part))
    check("... and it never passes the paid ceiling", jobs.ocr_time_limit(30, True, 900 * mb, 3.0) == jobs.TIME_LIMIT_S)
    # A 90 MB PDF of 200 pages with one scanned page: the old limit was the two minutes and 15 s the scan alone is
    # given; opening 90 MB and reading its text takes what any conversion of it is given.
    size = 90 * mb
    price, est = cu.price_for_job("pdf", "txt", size, 200, 1)
    plain_out = cu.estimate_output_bytes("pdf", "txt", size, 200)
    plain = jobs.time_limit_for(cu.billable_bytes(size, plain_out), cu.work_factor("pdf", "txt"), paid=True)
    limit = cu.ocr_limit("pdf", "txt", size, est, 1, True, 200)
    check("a 90 MB PDF of 200 pages with 1 scanned page is given at least the TXT limit of a PDF without one, plus a page's 15 s",
          plain > jobs.TIME_BASE_S and limit >= plain + oc.OCR_PAGE_S, f"{limit} vs {plain} + {oc.OCR_PAGE_S}")
    check("... and its price is made from exactly that limit",
          price == pricing.price(pricing.cost(limit, jobs.big_send_limit(est))), f"{price}")

    # The price is made from that limit, and only from it.
    for count in (2, 7, 30):
        price, out = cu.price_for_job("pdf", "txt", 300_000, count, count)
        want = pricing.price(pricing.cost(cu.ocr_limit("pdf", "txt", 300_000, out, count, True, count), jobs.big_send_limit(out)))
        check(f"the price of {count} scanned pages is the price of the time limit they are given", price == want,
              f"{price} vs {want}")
    price2 = cu.price_for_job("pdf", "txt", 300_000, 2, 2)[0]
    price30 = cu.price_for_job("pdf", "txt", 300_000, 30, 30)[0]
    check("more pages cost more", price30 > price2, f"{price2} vs {price30}")
    price, out = cu.price_for_job("png", "ocrpdf", over_bytes)
    check("a picture over 3 MB to a searchable PDF is priced from the one-page limit",
          price == pricing.price(pricing.cost(cu.ocr_limit("png", "ocrpdf", over_bytes, out, 1, True), jobs.big_send_limit(out))))
    check("the estimates: TXT 4 KB a page, a searchable PDF its input plus 10 KB a scanned page",
          cu.price_for_job("pdf", "txt", 300_000, 9, 3)[1] == 4096 * 9
          and cu.price_for_job("pdf", "ocrpdf", 300_000, 9, 3)[1] == 300_000 + 3 * 10240
          and cu.price_for_job("png", "ocrpdf", 1_000_000)[1] == int(1_000_000 * 1.2) + 10240)

    # A PDF that is not an OCR job is priced exactly as it was.
    for size, pages_known in ((200_000, 3), (2_000_000, 5), (5_000_000, 40), (80_000_000, 400)):
        old_out = cu.estimate_output_bytes("pdf", "txt", size, pages_known)
        old = (cu._price(size, old_out, cu.work_factor("pdf", "txt")), old_out)
        check(f"a {size // 1000} KB PDF with no scanned page counted is priced as before",
              cu.price_for_job("pdf", "txt", size, pages_known, 0) == old
              and cu.price_for_job("pdf", "txt", size, pages_known) == old, str(old))


# ---------------------------------------------------------------------------
# 3. The dispatcher: what the worker will and will not read
# ---------------------------------------------------------------------------

def dispatch():
    print("\n=== the worker reads what was sold, and nothing else ===")
    calls = []
    real_convert = oc.convert

    def spy(in_path, src_ext, target_ext, work_dir, stem="converted", lang="eng", max_scanned=None):
        calls.append({"src": src_ext, "target": target_ext, "lang": lang, "max": max_scanned})
        out = pathlib.Path(work_dir) / f"{stem}.{'txt' if target_ext == 'txt' else 'pdf'}"
        out.write_text("x", encoding="utf-8")
        return cu.Result(out)
    oc.convert = spy
    try:
        if pymupdf is None:
            skip("the dispatcher", "no PyMuPDF")
            return
        text_pdf = make_pdf(WORK / "texty.pdf", [("text", TEXT_PAGE)])
        cu.convert([text_pdf], "pdf", "txt", WORK, "d1", ocr_lang="rus", ocr_pages=0)
        check("pdf -> txt with no scanned pages counted never reaches the text recogniser", not calls, str(calls))
        cu.convert([text_pdf], "pdf", "txt", WORK, "d2", ocr_lang="rus", ocr_pages=3)
        check("... with pages counted it does, with the language and the number of pages",
              calls == [{"src": "pdf", "target": "txt", "lang": "rus", "max": 3}], str(calls))
        del calls[:]
        cu.convert([text_pdf], "pdf", "txt", WORK, "d2b", ocr_lang="rus", ocr_pages=oc.OCR_MAX_PAGES)
        check(f"... up to {oc.OCR_MAX_PAGES} pages", len(calls) == 1 and calls[0]["max"] == oc.OCR_MAX_PAGES, str(calls))
        del calls[:]
        cu.convert([text_pdf], "pdf", "txt", WORK, "d2c", ocr_lang="rus", ocr_pages=oc.OCR_MAX_PAGES + 1)
        check(f"... and with more than {oc.OCR_MAX_PAGES} it is today's TXT again, never reaching the text recogniser",
              not calls, str(calls))
        cu.convert([text_pdf], "pdf", "txt", WORK, "d2d", ocr_lang="rus", ocr_pages=500)
        check("... whatever the count", not calls, str(calls))
        del calls[:]
        cu.convert([text_pdf], "pdf", "ocrpdf", WORK, "d3", ocr_lang="uzb", ocr_pages=2)
        check("a searchable PDF is read for the pages counted",
              calls == [{"src": "pdf", "target": "ocrpdf", "lang": "uzb", "max": 2}], str(calls))
        del calls[:]
        cu.convert([text_pdf], "pdf", "ocrpdf", WORK, "d4", ocr_lang="uzb", ocr_pages=0)
        check("... and with none counted it is asked for nothing", calls == [{"src": "pdf", "target": "ocrpdf",
                                                                           "lang": "uzb", "max": 0}], str(calls))
        del calls[:]
        picture = WORK / "pic.png"
        picture.write_bytes(text_png(EN))
        cu.convert([picture], "png", "txt", WORK, "d5", ocr_lang="rus", ocr_pages=0)
        check("a picture is one page whatever the job says", calls == [{"src": "png", "target": "txt",
                                                                       "lang": "rus", "max": 1}], str(calls))
        del calls[:]
        for src, target in (("png", "txt"), ("png", "ocrpdf"), ("pdf", "ocrpdf")):
            failure = None
            try:
                cu.convert([picture, picture], src, target, WORK, "d6")
            except cu.ConversionError as exc:
                failure = exc
            check(f"an album is never read ({src} -> {target})", failure is not None and not calls, repr(failure))
        failure = None
        try:
            cu.convert([WORK / "pic.png"], "dng", "ocrpdf", WORK, "d7")
        except cu.ConversionError as exc:
            failure = exc
        check("a raw photo has no searchable PDF", failure is not None and not calls, repr(failure))
    finally:
        oc.convert = real_convert

    if pymupdf is None:
        return
    blank3 = make_pdf(WORK / "blank3.pdf", [("paper",)] * 3)
    if oc.tessdata_dir() is None:
        skip("a job sold for fewer pages than the file has", "no language data")
        return
    failure = None
    try:
        cu.convert([blank3], "pdf", "txt", WORK, "d8", ocr_pages=1)
    except cu.ConversionError as exc:
        failure = exc
    check("a file with more scanned pages than the job was sold is refused before any is read",
          failure is not None and "priced" in str(failure), repr(failure))
    failure = None
    try:
        cu.convert([blank3], "pdf", "txt", WORK, "d9", ocr_pages=3)
    except cu.ConversionError as exc:
        failure = exc
    check("... and with the right count it goes on to read them", failure is not None and "No text was found" in str(failure),
          repr(failure))


# ---------------------------------------------------------------------------
# 4. The format menu
# ---------------------------------------------------------------------------

async def menus():
    print("\n=== the format menu ===")
    if not CAN_READ:
        skip("the menus", "needs PyMuPDF with the engine and the language data")
        return
    mixed = make_pdf(WORK / "mixed.pdf", [("text", TEXT_PAGE), ("scan", EN), ("scan", EN)])
    text_only = make_pdf(WORK / "textonly.pdf", [("text", TEXT_PAGE), ("text", TEXT_PAGE)])
    one_scan = make_pdf(WORK / "onescan.pdf", [("scan", EN)])
    many = make_pdf(WORK / "many.pdf", [("paper",)] * (oc.OCR_MAX_PAGES + 1))
    full = make_pdf(WORK / "full.pdf", [("paper",)] * oc.OCR_MAX_PAGES)
    picture = WORK / "menu.png"
    picture.write_bytes(text_png(EN))
    unstaged = {}
    for name in ("mixed", "text_only", "one_scan", "many", "full", "picture"):
        src = {"mixed": mixed, "text_only": text_only, "one_scan": one_scan, "many": many, "full": full,
               "picture": picture}[name]
        copy = pathlib.Path(STAGE) / f"{USER.id}-{name}{src.suffix}"
        shutil.copy(src, copy)
        unstaged[name] = copy

    # --- a PDF with scanned pages ---
    with recording_workers() as seen:
        message, context = await open_menu(unstaged["mixed"], "pdf")
    state = context.user_data["convert"]
    text, markup = latest_menu(message)
    check("the pages are counted by a child process asking for describe_pdf",
          [p.get("kind") for p in seen] == ["describe_pdf"], str(seen))
    check("the menu knows a PDF's pages and its scanned pages", state["pages"] == 3 and state["ocr_pages"] == 2,
          f"{state['pages']} / {state['ocr_pages']}")
    check("... and offers TXT and the searchable PDF on the first screen",
          ["convfmt:txt", "convfmt:ocrpdf"] == [d for d in data_of(markup) if d in ("convfmt:txt", "convfmt:ocrpdf")],
          str(data_of(markup)))
    labels = dict((data, label) for label, data in flat(markup))
    check("the button says Searchable PDF, not ocrpdf", labels.get("convfmt:ocrpdf") == "Searchable PDF"
          and "ocrpdf" not in " ".join(label for label, _ in flat(markup)).lower(), str(labels))
    check("the text-language row is there, in English by default",
          labels.get("convocr") == "🔤 Text language: English (tap to change)", str(labels.get("convocr")))

    # --- the language toggle ---
    cycle = []
    for _ in range(4):
        query = FakeQuery("convocr", message)
        await bot.convert_ocr_callback(update_for(query), context)
        cycle.append((context.user_data.get("ocr_lang"), query.answers[-1][0]))
    check("a tap cycles English -> Russian -> Uzbek -> English, and says so",
          cycle == [("rus", "Text language is now Russian."), ("uzb", "Text language is now Uzbek (Latin and Cyrillic)."),
                    ("eng", "Text language is now English."), ("rus", "Text language is now Russian.")], str(cycle))
    redraw_text, redraw = EDITS[-1]
    check("... redrawing the menu in place with the new language on the row",
          dict((d, l) for l, d in flat(redraw)).get("convocr") == "🔤 Text language: Russian (tap to change)")
    check("... and the menu text stays", redraw_text == text, redraw_text[:80])
    for lang, first in (("ru", "uzb"), ("uz", "eng"), ("en", "rus")):
        context_l = new_context(lang)
        context_l.user_data["convert"] = dict(state)
        query = FakeQuery("convocr", message)
        await bot.convert_ocr_callback(update_for(query), context_l)
        check(f"the default follows the person's language ({lang}): the first tap gives {first}",
              context_l.user_data.get("ocr_lang") == first, str(context_l.user_data.get("ocr_lang")))
    check("the default choices", (bot._ocr_choice(new_context("en"), "en"), bot._ocr_choice(new_context("ru"), "ru"),
                                  bot._ocr_choice(new_context("uz"), "uz")) == ("eng", "rus", "uzb"))
    check("a remembered choice beats the default, and a nonsense one is ignored",
          bot._ocr_choice(new_context("en", ocr_lang="uzb"), "en") == "uzb"
          and bot._ocr_choice(new_context("ru", ocr_lang="klingon"), "ru") == "rus")
    for lang in i18n.SUPPORTED_LANGUAGES:
        rows = bot._format_rows(lang, ["png"], False, 1, ocr="uzb")
        label = dict((d, l) for l, d in flat(bot.InlineKeyboardMarkup(rows))).get("convocr")
        want = i18n.t(lang, "ocr_lang_toggle", name=i18n.t(lang, "ocr_lang_uzb"))
        check(f"{lang}: the row reads as the spec has it", label == want, str(label))
    stale = FakeQuery("convocr")
    await bot.convert_ocr_callback(update_for(stale), new_context("en"))
    check("a tap with no menu waiting says the conversion expired", stale.answers
          and stale.answers[-1] == (i18n.t("en", "conversion_expired"), True), str(stale.answers))

    # --- the quote and the refusals ---
    bot.family_link.star_balance = lambda user_id: 500
    for target in ("txt", "ocrpdf"):
        EDITS.clear()
        query = FakeQuery(f"convfmt:{target}", message)
        await bot.convert_format_chosen(update_for(query), context)
        price, est = cu.price_for_job("pdf", target, state["size"], state["pages"], state["ocr_pages"])
        quote = EDITS[-1][0] if EDITS else ""
        check(f"two scanned pages to {target}: a quote with the price from price_for_job",
              price > 0 and state["price"] == price and f"{price}" in quote, f"{price} / {quote[:100]}")
    check("the quote names the target as Searchable PDF", "PDF → Searchable PDF" in EDITS[-1][0], EDITS[-1][0][:60])
    state_after = context.user_data["convert"]
    check("... and keeps the pages the job will be sold for", state_after["ocr_pages"] == 2)

    # --- one scan: free ---
    message1, context1 = await open_menu(unstaged["one_scan"], "pdf")
    state1 = context1.user_data["convert"]
    started = []
    real_start = bot._start_chosen

    async def fake_start(update, ctx, conv_state, target_ext, lang):
        started.append((target_ext, conv_state["price"]))
    bot._start_chosen = fake_start
    try:
        query = FakeQuery("convfmt:ocrpdf", message1)
        await bot.convert_format_chosen(update_for(query), context1)
    finally:
        bot._start_chosen = real_start
    check("one scanned page of a small PDF is free and starts on the tap", started == [("ocrpdf", 0)], str(started))

    # --- a PDF with text only: nothing to read ---
    message2, context2 = await open_menu(unstaged["text_only"], "pdf")
    state2 = context2.user_data["convert"]
    _, markup2 = latest_menu(message2)
    check("a PDF with no scanned page counted: TXT yes, a searchable PDF and the language row no",
          state2["ocr_pages"] == 0 and "convfmt:txt" in data_of(markup2) and "convfmt:ocrpdf" not in data_of(markup2)
          and "convocr" not in data_of(markup2), str(data_of(markup2)))
    query = FakeQuery("convfmt:ocrpdf", message2)
    await bot.convert_format_chosen(update_for(query), context2)
    check("... and the searchable PDF is refused if it is asked for anyway",
          query.answers and query.answers[-1][1] is True and "target_ext" not in context2.user_data["convert"],
          str(query.answers))
    check("... in words that fit (not '.ocrpdf isn't a format I convert'), in all three languages",
          query.answers[-1][0] == i18n.t("en", "not_for_this_file")
          and "ocrpdf" not in query.answers[-1][0]
          and all(i18n.t(lg, "not_for_this_file") != i18n.t(lg, "unsupported_format", ext="x")
                  and "{" not in i18n.t(lg, "not_for_this_file") for lg in ("en", "uz", "ru")), str(query.answers))

    # --- more scanned pages than one conversion reads ---
    message3, context3 = await open_menu(unstaged["many"], "pdf")
    state3 = context3.user_data["convert"]
    text3, markup3 = latest_menu(message3)
    check(f"{oc.OCR_MAX_PAGES + 1} scanned pages: TXT is offered (the ordinary one), a searchable PDF and the language row are not",
          state3["ocr_pages"] == oc.OCR_MAX_PAGES + 1
          and "convfmt:txt" in data_of(markup3) and "convfmt:ocrpdf" not in data_of(markup3)
          and "convocr" not in data_of(markup3), str(data_of(markup3)))
    want_note = i18n.t("en", "ocr_too_many_pages_note", max=oc.OCR_MAX_PAGES)
    check("... and the menu says why", want_note in text3 and "recognised on at most" in want_note, text3[-300:])
    query = FakeQuery("convfmt:ocrpdf", message3)
    await bot.convert_format_chosen(update_for(query), context3)
    check("... a searchable PDF is refused if asked for anyway", query.answers and query.answers[-1][1] is True
          and "target_ext" not in context3.user_data["convert"], str(query.answers))
    started3 = []
    real_start3 = bot._start_chosen

    async def record_start(update, ctx, conv_state, target_ext, lang):
        started3.append((target_ext, conv_state["price"]))
    bot._start_chosen = record_start
    try:
        query = FakeQuery("convfmt:txt", message3)
        await bot.convert_format_chosen(update_for(query), context3)
    finally:
        bot._start_chosen = real_start3
    plain3 = cu.price_for_job("pdf", "txt", state3["size"], state3["pages"])
    check("... TXT starts on the tap, priced as the ordinary conversion of a PDF of that many pages",
          started3 == [("txt", plain3[0])] and state3["est_out"] == plain3[1]
          and cu.ocr_job_pages("pdf", "txt", state3["ocr_pages"], len(state3["paths"])) == 0,
          f"{started3} {plain3}")
    message4, context4 = await open_menu(unstaged["full"], "pdf")
    _, markup4 = latest_menu(message4)
    check(f"exactly {oc.OCR_MAX_PAGES} scanned pages are still offered, both",
          "convfmt:txt" in data_of(markup4) and "convfmt:ocrpdf" in data_of(markup4), str(data_of(markup4)))
    check("... without the note", i18n.t("en", "ocr_too_many_pages_note", max=oc.OCR_MAX_PAGES) not in message4.replies[-1])

    # --- pages with nothing to read are not scanned pages ---
    report = make_pdf(WORK / "report.pdf", [("text", TEXT_PAGE)] * 5 + [("blank",)] + [("text", TEXT_PAGE)] * 5 + [("blank",)])
    deck = make_pdf(WORK / "deck.pdf", [("text", "Chapter 3")] * (oc.OCR_MAX_PAGES + 1) + [("text", TEXT_PAGE)] * 9)
    for name, src, pages_n in (("report", report, 12), ("deck", deck, oc.OCR_MAX_PAGES + 10)):
        copy = pathlib.Path(STAGE) / f"{USER.id}-{name}.pdf"
        shutil.copy(src, copy)
        message_r, context_r = await open_menu(copy, "pdf")
        state_r = context_r.user_data["convert"]
        text_r, markup_r = latest_menu(message_r)
        check(f"a {name} with pages that hold nothing: {pages_n} pages, none scanned, TXT as before, no row, no note",
              state_r["pages"] == pages_n and state_r["ocr_pages"] == 0 and "convfmt:txt" in data_of(markup_r)
              and "convfmt:ocrpdf" not in data_of(markup_r) and "convocr" not in data_of(markup_r)
              and i18n.t("en", "ocr_too_many_pages_note", max=oc.OCR_MAX_PAGES) not in text_r, str(data_of(markup_r)))
        started_r = []

        async def record_r(update, ctx, conv_state, target_ext, lang):
            started_r.append((target_ext, conv_state["price"]))
        bot._start_chosen = record_r
        try:
            await bot.convert_format_chosen(update_for(FakeQuery("convfmt:txt", message_r)), context_r)
        finally:
            bot._start_chosen = real_start3
        check("... its TXT is free, exactly as it was before text was recognised",
              started_r == [("txt", 0)] and context_r.user_data["convert"]["est_out"]
              == cu.estimate_output_bytes("pdf", "txt", state_r["size"], pages_n), f"{started_r}")

    # --- a PDF whose pages were counted but whose scanned ones were not (the menu would not wait) ---
    real_describe = jobs.describe_pdf

    async def pages_only(path, timeout=None):
        return {"pages": 12, "scanned": None}
    jobs.describe_pdf = pages_only
    try:
        message_p, context_p = await open_menu(unstaged["mixed"], "pdf")
    finally:
        jobs.describe_pdf = real_describe
    state_p = context_p.user_data["convert"]
    _, markup_p = latest_menu(message_p)
    check("a PDF counted in pages but not in scanned pages: the pages are kept, nothing is read, TXT is today's",
          state_p["pages"] == 12 and state_p["ocr_pages"] == 0 and "convfmt:txt" in data_of(markup_p)
          and "convfmt:ocrpdf" not in data_of(markup_p) and "convocr" not in data_of(markup_p), str(data_of(markup_p)))
    check("... and priced from its page count, as TXT was",
          cu.price_for_job("pdf", "txt", state_p["size"], state_p["pages"], state_p["ocr_pages"])
          == cu.price_for_job("pdf", "txt", state_p["size"], 12) and state_p["pages"] == 12)

    # --- a picture ---
    message5, context5 = await open_menu(unstaged["picture"], "png")
    state5 = context5.user_data["convert"]
    _, markup5 = latest_menu(message5)
    check("a picture is one page to read", state5["ocr_pages"] == 1)
    check("... its first screen is the usual one, with no language row: none of its buttons reads text",
          [d for d in data_of(markup5) if d.startswith("convfmt:")]
          == ["convfmt:" + t for t in formats.popular_targets("png")]
          and "convfmt:txt" not in data_of(markup5) and "convocr" not in data_of(markup5), str(data_of(markup5)))
    query = FakeQuery("convmore", message5)
    await bot.convert_show_more(update_for(query), context5)
    expanded = EDITS[-1][1]
    check("More formats shows TXT and the searchable PDF, and the language row with them",
          "convfmt:txt" in data_of(expanded) and "convfmt:ocrpdf" in data_of(expanded)
          and "convocr" in data_of(expanded), str(data_of(expanded)))
    query = FakeQuery("convocr", message5)
    await bot.convert_ocr_callback(update_for(query), context5)
    check("changing the language keeps More formats open, and the row on it",
          "convless" in data_of(EDITS[-1][1]) and "convfmt:txt" in data_of(EDITS[-1][1])
          and "convocr" in data_of(EDITS[-1][1]), str(data_of(EDITS[-1][1])))
    query = FakeQuery("convless", message5)
    await bot.convert_show_more(update_for(query), context5)
    check("Fewer formats takes the row away again", "convmore" in data_of(EDITS[-1][1])
          and "convocr" not in data_of(EDITS[-1][1]), str(data_of(EDITS[-1][1])))
    query = FakeQuery("convocr", message5)
    await bot.convert_ocr_callback(update_for(query), context5)
    check("... and a stale tap on it redraws the closed view, still without the row",
          "convmore" in data_of(EDITS[-1][1]) and "convocr" not in data_of(EDITS[-1][1]), str(data_of(EDITS[-1][1])))
    # The other redraws follow the view they draw, as the first screen does.
    query = FakeQuery("convkeep", message5)
    await bot.convert_keep_callback(update_for(query), context5)
    check("the keep-file toggle redraws a picture's first screen without the row",
          "convocr" not in data_of(EDITS[-1][1]) and "convmore" in data_of(EDITS[-1][1]), str(data_of(EDITS[-1][1])))
    query = FakeQuery("convkeep", message)
    await bot.convert_keep_callback(update_for(query), context)
    check("... and a PDF with scanned pages' with it", "convocr" in data_of(EDITS[-1][1])
          and "convfmt:txt" in data_of(EDITS[-1][1]), str(data_of(EDITS[-1][1])))
    query = FakeQuery("convmore", message)
    await bot.convert_show_more(update_for(query), context)
    check("More formats on that PDF keeps the row", "convocr" in data_of(EDITS[-1][1]), str(data_of(EDITS[-1][1])))
    context.user_data["convert"]["target_ext"] = "txt"
    query = FakeQuery("convback", message)
    await bot.convert_quote_back(update_for(query), context)
    check("... and so does going back from a quote", "convocr" in data_of(EDITS[-1][1])
          and "convfmt:ocrpdf" in data_of(EDITS[-1][1]), str(data_of(EDITS[-1][1])))

    # --- an album is never read ---
    album = []
    for number in (1, 2):
        member = pathlib.Path(STAGE) / f"{USER.id}-album{number}.png"
        member.write_bytes(text_png(EN))
        album.append(member)
    message_a, context_a = FakeMessage(), new_context("en")
    await bot._present_conversion_options(message_a, context_a, USER, album, "png", group_id="g1")
    state_a = context_a.user_data["convert"]
    offered_a = bot._offered_targets(state_a)
    _, markup_a = latest_menu(message_a)
    check("an album of pictures is not offered TXT or a searchable PDF, nor the language row",
          "txt" not in offered_a and "ocrpdf" not in offered_a and "convocr" not in data_of(markup_a)
          and "jpg" in offered_a, str(offered_a))
    query = FakeQuery("convfmt:txt", message_a)
    await bot.convert_format_chosen(update_for(query), context_a)
    check("... and TXT is refused if asked for anyway", query.answers and query.answers[-1][1] is True
          and "target_ext" not in context_a.user_data["convert"], str(query.answers))
    twin = []
    for number in (1, 2):
        member = pathlib.Path(STAGE) / f"{USER.id}-twin{number}.pdf"
        shutil.copy(mixed, member)
        twin.append(member)
    message_t, context_t = FakeMessage(), new_context("en")
    await bot._present_conversion_options(message_t, context_t, USER, twin, "pdf", group_id="g2")
    offered_t = bot._offered_targets(context_t.user_data["convert"])
    check("an album of PDFs is converted as it was: TXT, no searchable PDF, no language row",
          "txt" in offered_t and "ocrpdf" not in offered_t and context_t.user_data["convert"]["ocr_pages"] == 0
          and "convocr" not in data_of(latest_menu(message_t)[1]), str(offered_t))

    # --- not a conversion that reads text: no row ---
    csv = pathlib.Path(STAGE) / f"{USER.id}-table.csv"
    csv.write_text("a,b\n1,2\n", encoding="utf-8")
    message6, _context6 = await open_menu(csv, "csv")
    _, markup6 = latest_menu(message6)
    check("a spreadsheet's menu has no text-language row", "convocr" not in data_of(markup6), str(data_of(markup6)))

    # --- a host without the language data ---
    with ocr_unmet():
        message7, context7 = await open_menu(unstaged["mixed"], "pdf")
        state7 = context7.user_data["convert"]
        _, markup7 = latest_menu(message7)
        check("without the language data nothing is counted, offered or priced as reading",
              state7["ocr_pages"] == 0 and "convfmt:ocrpdf" not in data_of(markup7) and "convocr" not in data_of(markup7)
              and "convfmt:txt" in data_of(markup7), str(data_of(markup7)))
        check("... and its TXT is priced as it was",
              cu.price_for_job("pdf", "txt", state7["size"], state7["pages"], state7["ocr_pages"])
              == (cu._price(state7["size"], cu.estimate_output_bytes("pdf", "txt", state7["size"], state7["pages"]),
                            cu.work_factor("pdf", "txt")),
                  cu.estimate_output_bytes("pdf", "txt", state7["size"], state7["pages"])))
        message8, context8 = await open_menu(unstaged["picture"], "png")
        _, markup8 = latest_menu(message8)
        check("... and a picture is offered neither", "convocr" not in data_of(markup8)
              and context8.user_data["convert"]["ocr_pages"] == 0)
        EDITS.clear()
        query = FakeQuery("convmore", message8)
        await bot.convert_show_more(update_for(query), context8)
        check("... not even under More formats", "convfmt:txt" not in data_of(EDITS[-1][1])
              and "convfmt:ocrpdf" not in data_of(EDITS[-1][1]))

    # --- a PDF whose pages could not be counted ---
    real_describe = jobs.describe_pdf

    async def unreadable(path, timeout=jobs.DESCRIBE_TIMEOUT_S):
        return None
    jobs.describe_pdf = unreadable
    try:
        message9, context9 = await open_menu(unstaged["mixed"], "pdf")
    finally:
        jobs.describe_pdf = real_describe
    state9 = context9.user_data["convert"]
    _, markup9 = latest_menu(message9)
    check("a PDF the child could not read gives today's menu: TXT, no searchable PDF, no row",
          state9["ocr_pages"] == 0 and state9["pages"] is None and "convfmt:txt" in data_of(markup9)
          and "convfmt:ocrpdf" not in data_of(markup9) and "convocr" not in data_of(markup9), str(data_of(markup9)))
    check("... priced and run as today's TXT",
          cu.ocr_job_pages("pdf", "txt", state9["ocr_pages"]) == 0
          and cu.price_for_job("pdf", "txt", state9["size"], state9["pages"], state9["ocr_pages"])
          == cu.price_for_job("pdf", "txt", state9["size"], None))


class FakeTgFile:
    def __init__(self, payload):
        self.payload = payload

    async def download_to_drive(self, custom_path):
        pathlib.Path(custom_path).write_bytes(self.payload)
        return custom_path


class FakeTgBot:
    """Telegram's end of a download: every file is the same bytes."""

    def __init__(self, payload):
        self.payload = payload

    async def get_file(self, file_id):
        return FakeTgFile(self.payload)

    async def delete_message(self, *a, **k):
        return True

    async def edit_message_text(self, *a, **k):
        return True


class UploadMessage(FakeMessage):
    """A message with a file in it, as convert_receive_file reads one."""

    def __init__(self, message_id, chat_id=7):
        super().__init__(chat_id)
        self.message_id = message_id
        self.document = self.photo = self.video = self.audio = self.voice = self.animation = None
        self.media_group_id = None


class FakeScheduled:
    def __init__(self, callback, name, data):
        self.callback, self.name, self.data, self.removed = callback, name, data, False

    def schedule_removal(self):
        self.removed = True


class FakeJobQueue:
    """Runs nothing on its own: the test fires what is left, which is "the album stopped arriving"."""

    def __init__(self):
        self.jobs = []

    def get_jobs_by_name(self, name):
        return [job for job in self.jobs if job.name == name and not job.removed]

    def run_once(self, callback, when, name=None, data=None, **kwargs):
        job = FakeScheduled(callback, name, data)
        self.jobs.append(job)
        return job


async def fire_pending(context):
    for job in [job for job in context.job_queue.jobs if not job.removed]:
        context.job = job
        await job.callback(context)
    context.job_queue.jobs = []


def pdf_upload(number, group, size):
    message = UploadMessage(number)
    message.document = SimpleNamespace(file_id=f"f{number}", file_size=size, file_name=f"scan{number}.pdf")
    message.media_group_id = group
    return SimpleNamespace(message=message, effective_user=USER)


async def album_race():
    print("\n=== a file joins an album after its menu was drawn ===")
    if not CAN_READ:
        skip("the album race", "needs PyMuPDF with the engine and the language data")
        return
    # Two scanned pages in a small PDF: read as text it is priced (more than one page), as an album's PDF it is free.
    source = make_pdf(WORK / "race.pdf", [("text", TEXT_PAGE), ("scan", EN), ("scan", EN)])
    payload = source.read_bytes()
    real_live, real_start, real_submit, real_runner = bot.LiveMessage, bot._start_chosen, jobs.submit, bot._RUNNER

    class Status:
        @classmethod
        async def reply_to(cls, message, text, **kwargs):
            return cls()

        async def set(self, *a, **k):
            return self

        async def delete(self, *a, **k):
            return None
    started, queued = [], []

    async def record_start(update, ctx, conv_state, target_ext, lang):
        started.append((target_ext, conv_state["price"], len(conv_state["paths"])))
    bot.LiveMessage, bot._start_chosen = Status, record_start
    jobs.submit = lambda application, job, runner: queued.append(job)
    bot.family_link.star_balance = lambda user_id: 500
    context = new_context("en")
    context.bot, context.job_queue = FakeTgBot(payload), FakeJobQueue()
    bot._RUNNER = SimpleNamespace(application=context.application, stop_keyboard=lambda job: None)
    try:
        first = pdf_upload(1, "G9", len(payload))
        await bot.convert_receive_file(first, context)
        await fire_pending(context)
        state = context.user_data["convert"]
        check("the first PDF of an album draws its menu on its own, with its pages and its scanned pages counted",
              len(state["paths"]) == 1 and state["group_id"] == "G9" and state["pages"] == 3 and state["ocr_pages"] == 2
              and "convocr" in data_of(first.message.keyboards[-1]), str({k: state.get(k) for k in ("paths", "pages", "ocr_pages")}))

        await bot.convert_receive_file(pdf_upload(2, "G9", len(payload)), context)
        state = context.user_data["convert"]
        check("a second file of the same album joins the pending conversion", len(state["paths"]) == 2, str(state["paths"]))
        check("... and the state forgets the first file's page count and scanned pages before the menu is drawn again",
              state["ocr_pages"] == 0 and state["pages"] is None, f"{state['ocr_pages']} {state['pages']}")

        # A tap on the menu that is still on the screen.
        query = FakeQuery("convfmt:txt", first.message)
        await bot.convert_format_chosen(update_for(query), context)
        check("TXT tapped on that menu is the album's ordinary TXT: free, not priced for pages it will not read",
              started == [("txt", 0, 2)], f"{started} {query.answers}")
        await bot._enqueue(context, first.message, USER, "en", state, "txt")
        job = queued[-1]
        check("... and it is queued as one: no page to read, no language, the free time limit, both files",
              (job.ocr_pages, job.ocr_lang, job.time_limit, len(job.paths)) == (0, "", jobs.FREE_TIME_LIMIT_S, 2),
              str((job.ocr_pages, job.ocr_lang, job.time_limit, len(job.paths))))
        bot._discard_pending(context)

        # The same, with the state as it was before the join was written: the number of files alone decides.
        context.user_data["convert"] = {"state": "awaiting_format", "paths": [], "src_ext": "pdf", "size": 2 * len(payload),
                                        "pages": 3, "ocr_pages": 2, "group_id": "G10", "animated": None}
        for number in (3, 4):
            copy = pathlib.Path(STAGE) / f"{USER.id}-stale{number}.pdf"
            copy.write_bytes(payload)
            context.user_data["convert"]["paths"].append(str(copy))
        del started[:]
        await bot.convert_format_chosen(update_for(FakeQuery("convfmt:txt", first.message)), context)
        state = context.user_data["convert"]
        check("a state of two files that still holds scanned pages is priced and started as an album's all the same "
              "(the price and the queue are given the number of files)", started == [("txt", 0, 2)], str(started))
        await bot._enqueue(context, first.message, USER, "en", state, "txt")
        check("... and queued as an album's", queued[-1].ocr_pages == 0 and queued[-1].ocr_lang == ""
              and queued[-1].time_limit == jobs.FREE_TIME_LIMIT_S)
        for path in state["paths"]:
            pathlib.Path(path).unlink(missing_ok=True)
    finally:
        bot.LiveMessage, bot._start_chosen, jobs.submit, bot._RUNNER = real_live, real_start, real_submit, real_runner
        bot._discard_pending(context)
    source_text = (HERE.parent / "convert_bot" / "bot.py").read_text(encoding="utf-8")
    tree = ast.parse(source_text)
    joins = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_join_album"]
    check("the upload handler joins a file to its album through the one function that forgets the pages", len(joins) == 1)


# ---------------------------------------------------------------------------
# 5. The queue: the language, the pages, the time limit
# ---------------------------------------------------------------------------

async def queueing():
    print("\n=== what the queue is given ===")
    captured = []
    real_submit, real_runner = jobs.submit, bot._RUNNER
    jobs.submit = lambda application, job, runner: captured.append(job)

    async def enqueue(state, target, lang="en", **user_data):
        context = new_context(lang, **user_data)
        bot._RUNNER = SimpleNamespace(application=context.application, stop_keyboard=lambda job: None)
        message = FakeMessage()
        base = {"paths": [str(WORK / "q.pdf")], "state": "awaiting_format", "est_out": 5000}
        base.update(state)
        del captured[:]
        await bot._enqueue(context, message, USER, lang, base, target)
        return captured[-1], message

    try:
        job, _ = await enqueue({"src_ext": "pdf", "size": 200_000, "price": 0, "ocr_pages": 1, "pages": 1}, "txt")
        check("one scanned page, free: the free time limit, the pages and the language",
              (job.time_limit, job.ocr_pages, job.ocr_lang) == (jobs.FREE_TIME_LIMIT_S, 1, "eng"),
              str((job.time_limit, job.ocr_pages, job.ocr_lang)))
        price, est = cu.price_for_job("pdf", "txt", 200_000, 2, 2)
        job, _ = await enqueue({"src_ext": "pdf", "size": 200_000, "price": price, "ocr_pages": 2, "pages": 2, "est_out": est},
                               "ocrpdf", lang="ru")
        check("two scanned pages, paid: the OCR time limit, two pages, and Russian from the person's language",
              (job.time_limit, job.ocr_pages, job.ocr_lang) == (jobs.ocr_time_limit(2, True), 2, "rus"),
              str((job.time_limit, job.ocr_pages, job.ocr_lang)))
        check("... and the price is exactly the price of the limit the job was given",
              price == pricing.price(pricing.cost(job.time_limit, jobs.big_send_limit(est))), f"{price}")
        job, _ = await enqueue({"src_ext": "pdf", "size": 200_000, "price": price, "ocr_pages": 2, "pages": 2, "est_out": est},
                               "txt", lang="en", ocr_lang="uzb")
        check("a chosen language travels with the job", job.ocr_lang == "uzb", job.ocr_lang)
        job, _ = await enqueue({"src_ext": "png", "size": 4_000_000, "price": 9, "ocr_pages": 1, "est_out": 4_000_000}, "ocrpdf")
        want = jobs.time_limit_for(cu.billable_bytes(4_000_000, 4_000_000), cu.work_factor("png", "ocrpdf"), True) + oc.OCR_PAGE_S
        check("a picture is one page, timed by the same function: what any conversion of the file is given, plus the page's",
              (job.ocr_pages, job.time_limit) == (1, want), str((job.ocr_pages, job.time_limit, want)))

        # The quote and the queue take the limit from one function, from the same numbers.
        mb = 1024 * 1024
        for src, target, size, pages_n, scanned in (("pdf", "txt", 90 * mb, 200, 1), ("pdf", "ocrpdf", 90 * mb, 200, 1),
                                                    ("pdf", "txt", 12 * mb, 40, 7), ("pdf", "ocrpdf", 40 * mb, 40, 30),
                                                    ("png", "txt", 8 * mb, None, 1), ("jpg", "ocrpdf", 15 * mb, None, 1),
                                                    ("pdf", "txt", 300_000, 5, 5)):
            price, est = cu.price_for_job(src, target, size, pages_n, scanned)
            job, _ = await enqueue({"src_ext": src, "size": size, "price": price, "ocr_pages": scanned,
                                    "pages": pages_n, "est_out": est}, target)
            made_from = pricing.price(pricing.cost(job.time_limit, jobs.big_send_limit(est)))
            check(f"{src} -> {target}, {size // mb or '0.3'} MB, {scanned} scanned: the job runs under the limit its price was made from",
                  (price == 0 and job.time_limit == jobs.FREE_TIME_LIMIT_S) or price == made_from,
                  f"price {price}, limit {job.time_limit}, from the limit {made_from}")
        price, est = cu.price_for_job("pdf", "txt", 90 * mb, 200, 1)
        job, _ = await enqueue({"src_ext": "pdf", "size": 90 * mb, "price": price, "ocr_pages": 1, "pages": 200, "est_out": est}, "txt")
        plain = jobs.time_limit_for(cu.billable_bytes(90 * mb, cu.estimate_output_bytes("pdf", "txt", 90 * mb, 200)),
                                    cu.work_factor("pdf", "txt"), paid=True)
        check("a 90 MB PDF of 200 pages with 1 scanned page: queued under at least the TXT limit of a PDF without one plus 15 s",
              job.ocr_pages == 1 and job.time_limit >= plain + oc.OCR_PAGE_S, f"{job.time_limit} vs {plain}")

        # More scanned pages than one conversion reads: today's TXT, timed and priced as it always was.
        too_many = oc.OCR_MAX_PAGES + 1
        price, est = cu.price_for_job("pdf", "txt", 5_000_000, 40, too_many)
        job, _ = await enqueue({"src_ext": "pdf", "size": 5_000_000, "price": price, "ocr_pages": too_many, "pages": 40,
                                "est_out": est}, "txt", lang="ru", ocr_lang="uzb")
        want = jobs.time_limit_for(cu.billable_bytes(5_000_000, est), cu.work_factor("pdf", "txt"), paid=True)
        check(f"a PDF of {too_many} scanned pages: its TXT is priced as the ordinary one and queued with no page, no language, "
              "and the ordinary time limit",
              (price, est) == cu.price_for_job("pdf", "txt", 5_000_000, 40, 0) and price > 0
              and (job.ocr_pages, job.ocr_lang, job.time_limit) == (0, "", want), str((job.ocr_pages, job.ocr_lang, job.time_limit)))

        # An album: never read, whatever its state says (the menu was drawn for the first file alone).
        album = [str(WORK / "q.pdf"), str(WORK / "q2.pdf")]
        price, est = cu.price_for_job("pdf", "txt", 5_000_000, None, 2, 2)
        check("the price of an album of PDFs is the ordinary one even when the state still holds scanned pages",
              (price, est) == cu.price_for_job("pdf", "txt", 5_000_000, None, 0), str((price, est)))
        job, _ = await enqueue({"paths": album, "src_ext": "pdf", "size": 5_000_000, "price": price, "ocr_pages": 2, "pages": None,
                                "est_out": est}, "txt", lang="ru", ocr_lang="uzb")
        want = jobs.time_limit_for(cu.billable_bytes(5_000_000, est), cu.work_factor("pdf", "txt"), paid=True)
        check("... and it is queued with no page, no language and the ordinary time limit",
              (job.ocr_pages, job.ocr_lang, job.time_limit) == (0, "", want), str((job.ocr_pages, job.ocr_lang, job.time_limit)))
        # Not an OCR job: nothing about reading text on it, and timed as it always was.
        state = {"src_ext": "pdf", "size": 5_000_000, "price": 30, "ocr_pages": 0, "pages": 40, "est_out": 250_000}
        job, _ = await enqueue(state, "txt", lang="ru", ocr_lang="uzb")
        want = jobs.time_limit_for(cu.billable_bytes(5_000_000, 250_000), cu.work_factor("pdf", "txt"), paid=True)
        check("a PDF with no scanned page counted: no pages, no language, today's time limit",
              (job.ocr_pages, job.ocr_lang, job.time_limit) == (0, "", want), str((job.ocr_pages, job.ocr_lang, job.time_limit)))
        job, _ = await enqueue({"src_ext": "pdf", "size": 5_000_000, "price": 30, "pages": 40, "est_out": 250_000}, "png", lang="ru")
        check("... and so is any other conversion", (job.ocr_pages, job.ocr_lang) == (0, ""))
        del EDITS[:]
        job, message = await enqueue({"src_ext": "png", "size": 4_000, "price": 0, "ocr_pages": 1}, "ocrpdf", lang="ru")
        status = EDITS[-1][0] if EDITS else ""
        check("the status text says PDF с текстом in Russian, never ocrpdf",
              "PDF с текстом" in status and "ocrpdf" not in status.lower(), status)
    finally:
        jobs.submit, bot._RUNNER = real_submit, real_runner


# ---------------------------------------------------------------------------
# 6. The pages are counted in a process of their own
# ---------------------------------------------------------------------------

async def describing():
    print("\n=== a stranger's PDF is opened in a child process ===")
    if pymupdf is None:
        skip("describing", "no PyMuPDF")
        return
    mixed = make_pdf(WORK / "d-mixed.pdf", [("text", TEXT_PAGE), ("scan", EN), ("scan", RU)])
    junk = WORK / "junk.pdf"
    junk.write_bytes(b"%PDF-1.7 this is not a PDF")
    answer = await jobs.describe_pdf(mixed)
    check("the child counts pages and scanned pages", answer == {"pages": 3, "scanned": 2}, str(answer))
    check("a file that is not a PDF gives None", await jobs.describe_pdf(junk) is None)

    # Not here: neither the bot's process nor this one opens the file.
    opened = []
    in_process = []
    real_open, real_count = pymupdf.open, oc.count_scanned
    pymupdf.open = lambda *a, **k: (opened.append(a), real_open(*a, **k))[1]
    oc.count_scanned = lambda *a, **k: (in_process.append(a), real_count(*a, **k))[1]
    try:
        with recording_workers() as seen:
            answer = await jobs.describe_pdf(mixed)
    finally:
        pymupdf.open, oc.count_scanned = real_open, real_count
    check("describe_pdf asks a child (kind describe_pdf)", [p.get("kind") for p in seen] == ["describe_pdf"], str(seen))
    check("... and never opens the PDF in this process", not opened and not in_process and answer == {"pages": 3, "scanned": 2},
          f"{opened} {in_process}")
    if CAN_READ:
        shutil.copy(mixed, pathlib.Path(STAGE) / f"{USER.id}-spy.pdf")
        opened_by_menu = []
        pymupdf.open = lambda *a, **k: (opened_by_menu.append(a), real_open(*a, **k))[1]
        try:
            await open_menu(pathlib.Path(STAGE) / f"{USER.id}-spy.pdf", "pdf")
        finally:
            pymupdf.open = real_open
        check("the menu for a PDF never has MuPDF open it in the bot's process", not opened_by_menu, str(opened_by_menu))
    else:
        skip("the menu opens no PDF itself", "no language data")

    # A child that dies, hangs or talks nonsense is the menu without the numbers.
    real_start = jobs._start_worker

    async def ask(code, timeout=jobs.DESCRIBE_TIMEOUT_S):
        async def start(payload):
            return await asyncio.create_subprocess_exec(sys.executable, "-c", code, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE)
        jobs._start_worker = start
        try:
            return await jobs.describe_pdf(mixed, timeout)
        finally:
            jobs._start_worker = real_start
    check("a child that crashes gives None", await ask("import os; os._exit(139)") is None)
    check("a child that raises gives None", await ask("raise RuntimeError('boom')") is None)
    started = time.monotonic()
    check("a child that hangs is killed at the timeout and gives None",
          await ask("import time; time.sleep(60)", timeout=1) is None and time.monotonic() - started < 10)
    for label, line in (("not JSON", "hello"), ("not an object", "[1, 2]"), ("a failure", '{"ok": false, "kind": "crash"}'),
                        ("negative pages", '{"ok": true, "pages": -1, "scanned": 0}'),
                        ("more scanned than pages", '{"ok": true, "pages": 2, "scanned": 3}'),
                        ("a float", '{"ok": true, "pages": 2.5, "scanned": 1}'),
                        ("a boolean", '{"ok": true, "pages": true, "scanned": 1}'),
                        ("a string", '{"ok": true, "pages": "2", "scanned": "1"}'),
                        ("missing scanned", '{"ok": true, "pages": 2}')):
        check(f"an answer that is {label} gives None", await ask("print(%r)" % line) is None)
    good = await ask("print('noise'); print(%r)" % json.dumps({"ok": True, "pages": 4, "scanned": 1}))
    check("noise before the answer is ignored", good == {"pages": 4, "scanned": 1}, str(good))

    # --- the child is on a deadline, and says the pages first ---
    print("  -- the deadline --")
    pages4 = json.dumps({"pages": 4})
    t0 = time.monotonic()
    cut = await ask("import time; print(%r, flush=True); time.sleep(60)" % pages4, timeout=1.5)
    check("a child that says the pages and then hangs is killed at the deadline: the pages, and no scanned count",
          cut == {"pages": 4, "scanned": None} and time.monotonic() - t0 < 8, f"{cut} {time.monotonic() - t0:.1f}s")
    check("... a child that says the pages and then crashes gives the same",
          await ask("import os; print(%r, flush=True); os._exit(139)" % pages4) == {"pages": 4, "scanned": None})
    check("... and one that says the pages and then fails",
          await ask("print(%r, flush=True); print(%r)" % (pages4, json.dumps({"ok": False, "kind": "crash", "message": "x"})))
          == {"pages": 4, "scanned": None})
    check("... and one that says the pages and then raises",
          await ask("print(%r, flush=True); raise RuntimeError('boom')" % pages4) == {"pages": 4, "scanned": None})
    check("a whole answer with no scanned count (the host cannot read text) is the pages and None",
          await ask("print(%r); print(%r)" % (pages4, json.dumps({"ok": True, "pages": 4, "scanned": None})))
          == {"pages": 4, "scanned": None})
    check("a whole answer is the last line's: the pages line before it is not what is taken",
          await ask("print(%r); print(%r)" % (pages4, json.dumps({"ok": True, "pages": 4, "scanned": 2})))
          == {"pages": 4, "scanned": 2})
    check("... a whole answer that is nonsense is None, whatever the pages line before it said",
          await ask("print(%r); print(%r)" % (pages4, json.dumps({"ok": True, "pages": 2, "scanned": 3}))) is None)
    for label, line in (("negative", {"pages": -1}), ("a boolean", {"pages": True}), ("a float", {"pages": 2.5}),
                        ("a string", {"pages": "4"}), ("missing", {"scanned": 1}), ("from a failure", {"ok": False, "pages": 4})):
        check(f"a pages line that is {label} is not taken",
              await ask("import time; print(%r, flush=True); time.sleep(60)" % json.dumps(line), timeout=1) is None)
    check("a hang that never said the pages is None, at the deadline",
          await ask("import time; time.sleep(60)", timeout=1) is None)
    # With no timeout given it is the module's setting, read when it is asked.
    real_seconds = oc.OCR_DESCRIBE_SECONDS
    oc.OCR_DESCRIBE_SECONDS = 1.5
    t0 = time.monotonic()
    try:
        cut = await ask("import time; print(%r, flush=True); time.sleep(60)" % pages4, timeout=None)
    finally:
        oc.OCR_DESCRIBE_SECONDS = real_seconds
    check("... without a timeout it waits OCR_DESCRIBE_SECONDS", cut == {"pages": 4, "scanned": None}
          and 1.0 < time.monotonic() - t0 < 8, f"{cut} {time.monotonic() - t0:.1f}s")

    if not CAN_READ:
        skip("the child on a PDF that takes minutes to look at", "needs PyMuPDF with the engine and the language data")
    else:
        bomb = glyph_bomb(WORK / "bomb.pdf")
        check("the test PDF is a few kilobytes", bomb.stat().st_size < 20_000, str(bomb.stat().st_size))
        # What the child writes first: the page count, within a few seconds, before it has looked at a page.
        proc = await jobs._start_worker({"kind": "describe_pdf", "path": str(bomb), "max_memory_mb": 0})
        try:
            first = await asyncio.wait_for(proc.stdout.readline(), timeout=oc.OCR_DESCRIBE_SECONDS)
        except asyncio.TimeoutError:
            first = b""
        finally:
            await jobs._kill(proc)
        check("the child writes its page count as the first line, long before it has looked at the page",
              first.strip() == b'{"pages": 1}', repr(first))
        check("... and is gone once it was killed", proc.returncode is not None)

        started_procs = []
        real_worker = jobs._start_worker

        async def spy_worker(payload):
            started_procs.append(await real_worker(payload))
            return started_procs[-1]
        jobs._start_worker = spy_worker
        t0 = time.monotonic()
        try:
            got = await jobs.describe_pdf(bomb)
        finally:
            jobs._start_worker = real_worker
        took = time.monotonic() - t0
        check("a 5 KB PDF whose page holds two million glyphs: describe_pdf answers at the deadline, not minutes later, "
              "with the pages known and scanned None",
              got == {"pages": 1, "scanned": None} and oc.OCR_DESCRIBE_SECONDS - 0.5 <= took < oc.OCR_DESCRIBE_SECONDS + 3,
              f"{got} after {took:.1f}s")
        check("... and the child was killed", started_procs and started_procs[0].returncode is not None)

        # Through the menu: the bot is not held, and the PDF is the ordinary PDF.
        staged = pathlib.Path(STAGE) / f"{USER.id}-bomb.pdf"
        shutil.copy(bomb, staged)
        t0 = time.monotonic()
        message_b, context_b = await open_menu(staged, "pdf")
        took = time.monotonic() - t0
        state_b = context_b.user_data["convert"]
        _, markup_b = latest_menu(message_b)
        check("the menu for it is drawn within the deadline, with the page count and no scanned page: TXT as before",
              took < oc.OCR_DESCRIBE_SECONDS + 3 and state_b["pages"] == 1 and state_b["ocr_pages"] == 0
              and "convfmt:txt" in data_of(markup_b) and "convfmt:ocrpdf" not in data_of(markup_b)
              and "convocr" not in data_of(markup_b), f"{took:.1f}s {state_b.get('pages')} {state_b.get('ocr_pages')}")

        # A PDF that is quick to look at is counted whole, and a blank page is not a scan.
        blanks = make_pdf(WORK / "d-blanks.pdf", [("blank",), ("text", TEXT_PAGE), ("blank",), ("scan", EN)])
        check("a normal PDF is still counted right: blank pages and text pages are not scans",
              await jobs.describe_pdf(blanks) == {"pages": 4, "scanned": 1})
        many_pages = make_pdf(WORK / "d-90.pdf", [("paper",)] * 90)
        counted = await jobs.describe_pdf(many_pages)
        check(f"90 scanned pages are counted as {oc.OCR_MAX_PAGES + 1}: the child stops looking once a conversion would not read them",
              counted == {"pages": 90, "scanned": oc.OCR_MAX_PAGES + 1}, str(counted))
        thirty_one = make_pdf(WORK / "d-31.pdf", [("paper",)] * (oc.OCR_MAX_PAGES + 1))
        thirty = make_pdf(WORK / "d-30.pdf", [("paper",)] * oc.OCR_MAX_PAGES)
        check(f"... exactly {oc.OCR_MAX_PAGES + 1} and {oc.OCR_MAX_PAGES} are counted as they are",
              await jobs.describe_pdf(thirty_one) == {"pages": oc.OCR_MAX_PAGES + 1, "scanned": oc.OCR_MAX_PAGES + 1}
              and await jobs.describe_pdf(thirty) == {"pages": oc.OCR_MAX_PAGES, "scanned": oc.OCR_MAX_PAGES})

        # A host that cannot read text has no use for the number: the child looks at nothing.
        nodata = tempfile.mkdtemp(prefix="ocrwire-nodata-")
        keep_tessdata = os.environ.get("TESSDATA_PREFIX")
        os.environ["TESSDATA_PREFIX"] = nodata
        try:
            t0 = time.monotonic()
            unread = await jobs.describe_pdf(bomb)
            quick = time.monotonic() - t0
            mixed_unread = await jobs.describe_pdf(mixed)
        finally:
            if keep_tessdata is None:
                os.environ.pop("TESSDATA_PREFIX", None)
            else:
                os.environ["TESSDATA_PREFIX"] = keep_tessdata
            shutil.rmtree(nodata, ignore_errors=True)
        check("with no language data the child says the pages and scanned null at once, and looks at no page",
              unread == {"pages": 1, "scanned": None} and quick < oc.OCR_DESCRIBE_SECONDS - 1
              and mixed_unread == {"pages": 3, "scanned": None}, f"{unread} {mixed_unread} {quick:.1f}s")

    source = (HERE.parent / "convert_bot" / "bot.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    asks = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "describe_pdf" and isinstance(n.func.value, ast.Name) and n.func.value.id == "jobs"]
    inside = [n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr in ("count_scanned", "inspect_pdf")]
    check("bot.py asks jobs.describe_pdf once and never reads a PDF itself", len(asks) == 1 and not inside,
          f"{len(asks)} asks, {len(inside)} direct")


# ---------------------------------------------------------------------------
# 7. Through the real worker
# ---------------------------------------------------------------------------

def job_for(path, src, target, lang="", pages=0):
    return jobs.Job(user_id=1, chat_id=1, paths=[str(path)], src_ext=src, target_ext=target,
                    size=pathlib.Path(path).stat().st_size, price=0, lang="en", ocr_lang=lang, ocr_pages=pages)


async def worker_runs():
    print("\n=== through the worker, into the job ===")
    if not CAN_READ:
        skip("the worker runs", "needs PyMuPDF with the engine and the language data")
        return
    russian = make_pdf(WORK / "ru.pdf", [("text", TEXT_PAGE), ("scan", RU)])
    english = make_pdf(WORK / "en.pdf", [("text", TEXT_PAGE), ("scan", EN)])
    only_scans = make_pdf(WORK / "scans.pdf", [("scan", EN)])
    three = make_pdf(WORK / "three.pdf", [("scan", EN), ("scan", EN), ("scan", EN)])

    with recording_workers() as seen:
        job = job_for(russian, "pdf", "txt", "rus", 1)
        outcome = await jobs.run_worker(job, WORK, "w-ru", 120)
    got = read_text(outcome.path) if outcome.ok else ""
    check("the language and the pages reach the worker in its payload",
          seen and seen[0].get("ocr_lang") == "rus" and seen[0].get("ocr_pages") == 1, str(seen))
    check("pdf -> txt with the pages counted: the text page verbatim and the scanned page read in Russian",
          outcome.ok and TEXT_PAGE in got and "Проверка" in got, f"{outcome.kind} {outcome.message} {got[:120]!r}")
    job = job_for(russian, "pdf", "txt", "eng", 1)
    outcome = await jobs.run_worker(job, WORK, "w-ru-eng", 120)
    got = read_text(outcome.path) if outcome.ok else ""
    check("... and in English it is not read as Russian", outcome.ok and "Проверка" not in got, got[:120])
    job = job_for(english, "pdf", "txt", "", 0)
    outcome = await jobs.run_worker(job, WORK, "w-unpriced", 120)
    got = read_text(outcome.path) if outcome.ok else ""
    check("pdf -> txt priced for no scanned page is today's conversion: the text page, no recognition",
          outcome.ok and TEXT_PAGE in got and "Invoice" not in got, f"{outcome.kind} {outcome.message} {got[:120]!r}")
    job = job_for(only_scans, "pdf", "txt", "", 0)
    outcome = await jobs.run_worker(job, WORK, "w-scans-unpriced", 120)
    check("... and a PDF of nothing but scans is refused as it always was",
          not outcome.ok and outcome.kind == jobs.REFUSED and "scanned pages" in outcome.message, f"{outcome.kind} {outcome.message}")
    job = job_for(three, "pdf", "txt", "eng", 1)
    outcome = await jobs.run_worker(job, WORK, "w-short", 120)
    check("a PDF with more scanned pages than the job was sold is refused, not read",
          not outcome.ok and outcome.kind == jobs.REFUSED and "priced" in outcome.message, f"{outcome.kind} {outcome.message}")
    job = job_for(only_scans, "pdf", "ocrpdf", "eng", 0)
    outcome = await jobs.run_worker(job, WORK, "w-ocrpdf-0", 120)
    check("a searchable PDF is not made for a job sold no pages", not outcome.ok and outcome.kind == jobs.REFUSED,
          f"{outcome.kind} {outcome.message}")

    picture = WORK / "ru.png"
    picture.write_bytes(text_png(RU, size=34))
    job = job_for(picture, "png", "txt", "rus", 1)
    outcome = await jobs.run_worker(job, WORK, "w-pic", 120)
    got = read_text(outcome.path) if outcome.ok else ""
    check("a picture to TXT is read in the language the job carries", outcome.ok and "Проверка" in got,
          f"{outcome.kind} {outcome.message} {got!r}")

    try:
        from PIL import Image
    except ImportError:
        Image = None
    if Image is None:
        skip("a TIFF of two pages through the worker", "no Pillow")
    else:
        import io
        page = Image.open(io.BytesIO(text_png(EN)))
        two_pages = WORK / "two-pages.tif"
        page.save(two_pages, save_all=True, append_images=[page])
        for target in ("txt", "ocrpdf"):
            outcome = await jobs.run_worker(job_for(two_pages, "tiff", target, "eng", 1), WORK, f"w-tiff-{target}", 120)
            check(f"a TIFF of two pages to {target}: refused through the worker, as any refusal is, with what to do instead",
                  not outcome.ok and outcome.kind == jobs.REFUSED and "That TIFF holds 2 pages" in outcome.message
                  and "convert the TIFF to PDF first" in outcome.message, f"{outcome.kind} {outcome.message}")
        one_page = WORK / "one-page.tif"
        page.save(one_page)
        outcome = await jobs.run_worker(job_for(one_page, "tiff", "txt", "eng", 1), WORK, "w-tiff-one", 120)
        check("... and a TIFF of one page is read", outcome.ok and "Invoice" in read_text(outcome.path),
              f"{outcome.kind} {outcome.message}")

    # --- the searchable PDF is a .pdf, and the source is not touched ---
    print("  -- the searchable PDF's file --")
    source = WORK / "scan.pdf"
    shutil.copy(only_scans, source)
    before = source.read_bytes()
    job = job_for(source, "pdf", "ocrpdf", "eng", 1)
    outcome = await jobs.run_worker(job, WORK, "scan-out", 120)
    out_path = pathlib.Path(outcome.path or "x")
    check("scan.pdf -> a searchable PDF is delivered as a PDF",
          outcome.ok and out_path.suffix == ".pdf" and out_path.read_bytes()[:5] == b"%PDF-", f"{outcome.kind} {outcome.message} {out_path}")
    check("... the file is not called after the pseudo-extension, and nothing of that name is left behind",
          not any("ocrpdf" in p.name.lower() for p in WORK.iterdir())
          and "ocrpdf" not in convert_runner.result_filename(outcome.path or "x.pdf").lower()
          and convert_runner.result_filename(outcome.path or "x.pdf").endswith(".pdf"))
    check("... the uploaded source is untouched, and is not the result", source.read_bytes() == before
          and out_path.resolve() != source.resolve())
    doc = pymupdf.open(str(out_path))
    check("... and holds the text, searchable", "Invoice" in doc[0].get_text(), doc[0].get_text()[:60])
    doc.close()
    job = job_for(source, "pdf", "ocrpdf", "eng", 1)
    outcome = await jobs.run_worker(job, WORK, "scan", 120)
    check("a result that would be the uploaded file is refused, and the upload survives",
          not outcome.ok and source.exists() and source.read_bytes() == before, f"{outcome.kind} {outcome.message}")
    picture_pdf = job_for(WORK / "ru.png", "png", "ocrpdf", "rus", 1)
    outcome = await jobs.run_worker(picture_pdf, WORK, "pic-out", 120)
    check("a picture to a searchable PDF is a .pdf too", outcome.ok and pathlib.Path(outcome.path).suffix == ".pdf",
          f"{outcome.kind} {outcome.message}")


# ---------------------------------------------------------------------------
# 8. What the person reads
# ---------------------------------------------------------------------------

def words():
    print("\n=== the texts ===")
    keys = ("ocr_lang_toggle", "ocr_lang_set", "ocr_lang_eng", "ocr_lang_rus", "ocr_lang_uzb",
            "ocr_too_many_pages_note", "target_ocrpdf")
    for lang in i18n.SUPPORTED_LANGUAGES:
        absent = [key for key in keys if key not in i18n.STRINGS[lang]]
        check(f"{lang}: every key is translated", not absent, str(absent))
        try:
            i18n.t(lang, "ocr_lang_toggle", name="x")
            i18n.t(lang, "ocr_lang_set", name="x")
            i18n.t(lang, "ocr_too_many_pages_note", n=31, max=30)
            ok = True
        except Exception as exc:
            ok = False
            print("   ", type(exc).__name__, exc)
        check(f"{lang}: the texts take their fields", ok)
    spec = {
        "en": ("Searchable PDF", "🔤 Text language: {name} (tap to change)", "Text language is now {name}.",
               "English", "Russian", "Uzbek (Latin and Cyrillic)"),
        "uz": ("Matnli PDF", "🔤 Matn tili: {name} (o'zgartirish uchun bosing)", "Endi matn tili: {name}.",
               "inglizcha", "ruscha", "o'zbekcha (lotin va kirill)"),
        "ru": ("PDF с текстом", "🔤 Язык текста: {name} (нажмите, чтобы сменить)", "Теперь язык текста: {name}.",
               "английский", "русский", "узбекский (латиница и кириллица)"),
    }
    for lang, (label, toggle, said, eng, rus, uzb) in spec.items():
        table = i18n.STRINGS[lang]
        check(f"{lang}: the texts are the spec's",
              (table["target_ocrpdf"], table["ocr_lang_toggle"], table["ocr_lang_set"], table["ocr_lang_eng"],
               table["ocr_lang_rus"], table["ocr_lang_uzb"]) == (label, toggle, said, eng, rus, uzb))
        check(f"{lang}: target_label gives the name for the searchable PDF and the capitals for the rest",
              i18n.target_label(lang, "ocrpdf") == label and i18n.target_label(lang, "png") == "PNG"
              and i18n.target_label(lang, None) == "?")
    notes = {
        "en": "⚠️ NOTE: more than {max} pages of this PDF are pictures without text. Text is recognised on at most {max} "
              "such pages here, so TXT gives only the text the other pages already have, and a searchable PDF is not offered.",
        "uz": "⚠️ ESLATMA: bu PDF ning {max} tadan ortiq sahifasi matnsiz rasm. Bu yerda ko'pi bilan {max} ta shunday "
              "sahifadagi matn aniqlanadi, shuning uchun TXT faqat boshqa sahifalarda bor matnni beradi, matnli PDF esa "
              "taklif qilinmaydi.",
        "ru": "⚠️ ВАЖНО: в этом PDF больше {max} страниц без текста (только картинка). Текст распознаётся максимум на {max} "
              "таких страницах, поэтому TXT содержит только текст остальных страниц, а PDF с текстом не предлагается.",
    }
    for lang, note in notes.items():
        check(f"{lang}: the note is the spec's, with the limit and no page count in it",
              i18n.STRINGS[lang]["ocr_too_many_pages_note"] == note and "{n}" not in note)
        check(f"{lang}: ... and takes only the limit", "30" in i18n.t(lang, "ocr_too_many_pages_note", max=30)
              and "31" not in i18n.t(lang, "ocr_too_many_pages_note", max=30))
    for lang, line in (("en", "📝 Text from a photo or a scanned PDF: send it and choose TXT or Searchable PDF."),
                       ("uz", "📝 Rasm yoki skanerlangan PDF dagi matn: faylni yuboring va TXT yoki Matnli PDF ni tanlang."),
                       ("ru", "📝 Текст с фото или скана PDF: отправьте файл и выберите TXT или PDF с текстом.")):
        check(f"{lang}: /help carries the line", line in i18n.t(lang, "help_brief"))

    # Nowhere a label is shown does the pseudo-extension appear.
    for lang in i18n.SUPPORTED_LANGUAGES:
        rows = bot._format_rows(lang, ["png", "txt", "ocrpdf"], True, 3, keep=False, ocr="eng")
        labels = [label for label, _ in flat(bot.InlineKeyboardMarkup(rows))]
        check(f"{lang}: the menu's buttons never say ocrpdf", "ocrpdf" not in " ".join(labels).lower()
              and i18n.target_label(lang, "ocrpdf") in labels, str(labels))
        shown = bot._format_lookup(lang, "pdf") or ""
        check(f"{lang}: /formats pdf never says ocrpdf, and names it in words where it is offered",
              "ocrpdf" not in shown.lower()
              and (i18n.target_label(lang, "ocrpdf") in shown) == ("ocrpdf" in formats.targets_for("pdf")), shown[:200])
        body, _ = bot._formats_category(lang, "image")
        check(f"{lang}: /formats for pictures never says ocrpdf", "ocrpdf" not in body.lower(), body[-200:])
        quote = bot._quote_text(lang, "pdf", "ocrpdf", 1000, 2000, 12, 50)
        check(f"{lang}: the quote names it in words", i18n.target_label(lang, "ocrpdf") in quote.splitlines()[0]
              and "ocrpdf" not in quote.lower(), quote.splitlines()[0])
        job = jobs.Job(user_id=1, chat_id=1, paths=["x"], src_ext="pdf", target_ext="ocrpdf", size=1, price=0, lang=lang)
        card = convert_runner.status_card(lang, job)
        check(f"{lang}: the status card never says ocrpdf", "ocrpdf" not in card.lower()
              and i18n.target_label(lang, "ocrpdf") in card, card)
        check(f"{lang}: the ledger line is words, not the pseudo-extension",
              convert_runner._pair(job) == "pdf->searchable-pdf")


def main():
    offered()
    pricing_and_limits()
    dispatch()
    words()

    async def everything():
        await menus()
        await album_race()
        await queueing()
        await describing()
        await worker_runs()
    asyncio.run(everything())


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
        shutil.rmtree(STAGE, ignore_errors=True)
    print()
    if failures:
        print(f"FAILURES: {len(failures)}")
        for name in failures:
            print("  -", name)
        sys.exit(1)
    print("All text-recognition wiring checks passed.")
