"""What 2.1.7 brought, ported to 2.2.0: ConvertBot's first download no longer times out (getFile is
given time and retried), failures that pass say "try again", an unsupported file is refused before it
is downloaded, PowerPoint / Word / Excel files convert through LibreOffice, and the operator's own
accounts are left out of the statistics. The office tests run the real LibreOffice and are skipped
where it is not installed."""
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from conftest import load

formats = load("convert_bot", "formats")
formats.probe()
convert_utils = load("convert_bot", "convert_utils")
office = load("convert_bot", "office_convert")
jobs = load("convert_bot", "jobs")
bot = load("convert_bot", "bot")
cv_i18n = load("convert_bot", "i18n")
dl_i18n = load("downloader_bot", "i18n")

from telegram.error import BadRequest, TimedOut  # noqa: E402

HAS_OFFICE = office.soffice_path() is not None
needs_office = pytest.mark.skipif(not HAS_OFFICE, reason="LibreOffice is not installed here")

def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── the download ────────────────────────────────────────────────────────────────────────────────

class FakeFile:
    def __init__(self, failures=0):
        self.file_path = ""
        self.failures = failures
        self.calls = []

    async def download_to_drive(self, custom_path=None, **timeouts):
        self.calls.append(timeouts)
        if self.failures:
            self.failures -= 1
            raise TimedOut()
        Path(custom_path).write_bytes(b"deck")


def fake_context(get_file):
    return SimpleNamespace(bot=SimpleNamespace(get_file=get_file))


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    real_sleep = asyncio.sleep

    async def instant(seconds, *args, **kwargs):
        return await real_sleep(0)
    monkeypatch.setattr(bot.asyncio, "sleep", instant)


def test_first_getfile_timeout_is_tried_again(tmp_path):
    tg_file = FakeFile()
    answers = [TimedOut(), tg_file]
    seen = []

    async def get_file(file_id, **kwargs):
        seen.append(kwargs)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    path = tmp_path / "in.pptx"
    run(bot._download_bot_api(fake_context(get_file), SimpleNamespace(file_id="x"), path, 90_000, None, "en"))
    assert path.read_bytes() == b"deck"
    assert len(seen) == 2
    # getFile waits for the Bot API server to fetch an uncached file, not the library's 5 s.
    assert all(call["read_timeout"] >= 60 for call in seen)
    assert tg_file.calls[0]["read_timeout"] >= 60


def test_download_timeout_is_tried_again(tmp_path):
    tg_file = FakeFile(failures=1)

    async def get_file(file_id, **kwargs):
        return tg_file

    path = tmp_path / "in.pdf"
    run(bot._download_bot_api(fake_context(get_file), SimpleNamespace(file_id="x"), path, 1000, None, "en"))
    assert path.read_bytes() == b"deck" and len(tg_file.calls) == 2


def test_gives_up_after_three_tries(tmp_path):
    calls = []

    async def get_file(file_id, **kwargs):
        calls.append(1)
        raise TimedOut()

    with pytest.raises(TimedOut):
        run(bot._download_bot_api(fake_context(get_file), SimpleNamespace(file_id="x"),
                                  tmp_path / "f", 10, None, "en"))
    assert len(calls) == bot.DOWNLOAD_TRIES == 3


def test_a_refusal_is_not_tried_again(tmp_path):
    calls = []

    async def get_file(file_id, **kwargs):
        calls.append(1)
        raise BadRequest("File is too big")

    with pytest.raises(BadRequest):
        run(bot._download_bot_api(fake_context(get_file), SimpleNamespace(file_id="x"),
                                  tmp_path / "f", 10, None, "en"))
    assert len(calls) == 1


def _update(file_name):
    replies = []

    async def reply_text(text, **kwargs):
        replies.append(text)
    document = SimpleNamespace(file_name=file_name, mime_type="", file_size=90_000, file_id="x")
    message = SimpleNamespace(animation=None, document=document, photo=None, video=None, audio=None,
                              voice=None, chat_id=1, reply_text=reply_text, media_group_id=None)
    update = SimpleNamespace(message=message, effective_user=SimpleNamespace(id=1))
    return update, replies


def test_unsupported_file_is_refused_before_downloading(monkeypatch):
    monkeypatch.setattr(bot.i18n, "get_lang", mock.AsyncMock(return_value="en"))
    get_file = mock.AsyncMock()
    update, replies = _update("song.xyz")
    context = SimpleNamespace(bot=SimpleNamespace(get_file=get_file), user_data={})
    run(bot.convert_receive_file(update, context))
    assert get_file.await_count == 0
    assert replies and '".xyz" isn\'t a supported format' in replies[0]


def test_office_file_is_refused_before_downloading_without_libreoffice(monkeypatch):
    monkeypatch.setattr(bot.i18n, "get_lang", mock.AsyncMock(return_value="en"))
    monkeypatch.setattr(formats, "_MET", formats._MET - {"office", "pdfimport"})
    get_file = mock.AsyncMock()
    update, replies = _update("slides.pptx")
    run(bot.convert_receive_file(update, SimpleNamespace(bot=SimpleNamespace(get_file=get_file), user_data={})))
    assert get_file.await_count == 0 and '".pptx" isn' in replies[0]


# ── "try again" ─────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("lang,words", [("en", "again"), ("uz", "qayta"), ("ru", "ещё раз")])
def test_passing_failures_say_try_again(lang, words):
    for key in ("download_failed", "job_reason_crash", "job_reason_send_failed"):
        text = cv_i18n.t(lang, key, error="Timed out")
        assert words in text.lower(), (lang, key, text)
    assert words in dl_i18n.t(lang, "download_failed", error="Timed out").lower()


def test_lasting_refusals_do_not_say_try_again():
    assert "again" not in cv_i18n.t("en", "unsupported_format", ext="xyz").lower()


# ── formats ─────────────────────────────────────────────────────────────────────────────────────

@needs_office
def test_office_targets():
    assert formats.category_of("pptx") == formats.PRESENTATION
    assert formats.category_of("pps") == formats.PRESENTATION  # a slide show is a deck
    assert set(formats.targets_for("pptx")) >= {"pdf", "ppt", "odp", "png", "jpg"}
    assert set(formats.targets_for("ppt")) >= {"pdf", "pptx"}
    assert "pptx" in formats.targets_for("pdf")
    assert "pptx" in formats.popular_targets("pdf")
    assert set(formats.targets_for("doc")) >= {"pdf", "docx", "odt"}
    assert set(formats.targets_for("docx")) >= {"pdf", "doc", "odt"}
    assert set(formats.targets_for("xls")) >= {"xlsx", "csv", "pdf", "ods"}
    assert set(formats.targets_for("xlsx")) >= {"xls", "ods", "csv"}
    for ext in ("pptx", "ppt", "odp", "doc", "odt", "rtf", "xls", "ods"):
        assert bot.i18n.t("en", f"category_{formats.category_of(ext)}")


def test_without_libreoffice_nothing_office_is_offered(monkeypatch):
    monkeypatch.setattr(formats, "_MET", formats._MET - {"office", "pdfimport"})
    for ext in ("pptx", "ppt", "odp", "doc", "odt", "rtf", "xls", "ods"):
        assert not formats.is_supported_source(ext) and formats.targets_for(ext) == []
    assert "pptx" not in formats.targets_for("pdf")
    assert {"doc", "odt", "rtf"}.isdisjoint(formats.targets_for("docx"))
    assert "pdf" in formats.targets_for("docx")  # the old way still works
    assert not office.handles("docx", "pdf")


def test_every_office_pair_has_a_price():
    for src in ("pptx", "ppt", "odp", "doc", "odt", "rtf", "xls", "ods"):
        for target in formats.declared_targets(src):
            credits, out_bytes = convert_utils.price_for_job(src, target, 8 * 1024 * 1024)
            assert credits >= 0 and out_bytes > 0


# ── LibreOffice ─────────────────────────────────────────────────────────────────────────────────

def _slides_pdf(path, pages=3):
    import pymupdf
    doc = pymupdf.open()
    for number in range(1, pages + 1):
        page = doc.new_page(width=960, height=540)
        page.insert_text((60, 120), f"Lecture slide {number}", fontsize=36)
    doc.save(path)
    doc.close()


def _convert(src, src_ext, target_ext, folder):
    work = folder / f"w-{src_ext}-{target_ext}"
    work.mkdir()
    result = convert_utils.convert([src], src_ext, target_ext, work, "converted")
    leftovers = sorted(p.name for p in work.iterdir() if p != result.path)
    assert leftovers == [], leftovers
    return result


def _pdf_text(path):
    import pymupdf
    with pymupdf.open(path) as doc:
        return doc.page_count, " ".join(page.get_text() for page in doc)


@needs_office
def test_ppt_pptx_pdf_round_trip(tmp_path):
    _slides_pdf(tmp_path / "lecture.pdf")
    pptx = _convert(tmp_path / "lecture.pdf", "pdf", "pptx", tmp_path).path
    ppt = _convert(pptx, "pptx", "ppt", tmp_path).path
    pptx_again = _convert(ppt, "ppt", "pptx", tmp_path).path
    odp = _convert(pptx_again, "pptx", "odp", tmp_path).path
    pdf = _convert(odp, "odp", "pdf", tmp_path).path
    pages, text = _pdf_text(pdf)
    assert pages == 3 and "Lecture slide 3" in text
    pictures = _convert(ppt, "ppt", "png", tmp_path)
    assert pictures.zipped and pictures.items == 3
    assert "Lecture slide 2" in _convert(pptx, "pptx", "txt", tmp_path).path.read_text()


@needs_office
def test_word_and_excel(tmp_path):
    (tmp_path / "essay.html").write_text("<h1>Essay</h1><p>Salom, привет, 안녕하세요</p>", encoding="utf-8")
    work = tmp_path / "make"
    work.mkdir()
    odt = office.soffice(tmp_path / "essay.html", "html", "odt", work)
    doc = _convert(odt, "odt", "doc", tmp_path).path
    docx = _convert(doc, "doc", "docx", tmp_path).path
    pdf = _convert(docx, "docx", "pdf", tmp_path).path  # LibreOffice now, Word's layout
    assert "привет" in _pdf_text(pdf)[1]
    assert "Essay" in _convert(docx, "docx", "rtf", tmp_path).path.read_text(errors="replace")

    import openpyxl
    book = openpyxl.Workbook()
    book.active.append(["name", "score"])
    book.active.append(["Ali", 90])
    book.save(tmp_path / "marks.xlsx")
    xls = _convert(tmp_path / "marks.xlsx", "xlsx", "xls", tmp_path).path
    ods = _convert(xls, "xls", "ods", tmp_path).path
    assert "Ali,90" in _convert(ods, "ods", "csv", tmp_path).path.read_text()
    assert json.loads(_convert(xls, "xls", "json", tmp_path).path.read_text())


@needs_office
def test_a_file_libreoffice_cannot_open_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(office, "_run", lambda *args, **kwargs: None)  # exits 0, writes nothing
    (tmp_path / "x.ppt").write_bytes(b"\x00" * 10)
    with pytest.raises(convert_utils.ConversionError, match="couldn't open"):
        office.soffice(tmp_path / "x.ppt", "ppt", "pdf", tmp_path)


@needs_office
@pytest.mark.parametrize("memory_mb", [0, 2048])
def test_through_the_worker_process(tmp_path, memory_mb):
    """The way jobs.py runs a conversion: a process of its own, perhaps with a memory cap."""
    _slides_pdf(tmp_path / "lecture.pdf", pages=2)
    job = {"in_paths": [str(tmp_path / "lecture.pdf")], "src_ext": "pdf", "target_ext": "pptx",
           "work_dir": str(tmp_path), "stem": "deck", "max_pixels": 1_000_000, "max_memory_mb": memory_mb}
    done = subprocess.run([sys.executable, "-u", str(jobs.WORKER), *jobs.WORKER_ARGS, json.dumps(job)],
                          cwd=str(jobs.HERE), capture_output=True, timeout=180)
    outcome = jobs._parse(done.returncode, done.stdout, done.stderr)
    assert outcome.ok, (outcome.kind, outcome.message, done.stderr[-2000:])
    assert Path(outcome.path).name == "deck.pptx" and outcome.bytes > 0
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith("office-")] == []


# ── what LibreOffice is given, and what it may reach ────────────────────────────────────────────

@needs_office
def test_libreoffice_gets_no_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("CBOT_TOKEN", "123:secret")
    monkeypatch.setenv("DATABASE_URL", "postgres://secret")
    seen = {}
    real = office.subprocess.run

    def spy(command, **kwargs):
        seen.update(kwargs.get("env") or {})
        return real(command, **kwargs)
    monkeypatch.setattr(office.subprocess, "run", spy)
    _slides_pdf(tmp_path / "s.pdf", pages=1)
    _convert(tmp_path / "s.pdf", "pdf", "pptx", tmp_path)
    assert seen and "CBOT_TOKEN" not in seen and "DATABASE_URL" not in seen
    assert not any("secret" in value for value in seen.values())


@needs_office
def test_a_document_reaches_no_file_and_no_address(tmp_path):
    import http.server
    import threading
    secret = tmp_path / "secret.txt"
    secret.write_text("CANARY-SECRET-TEXT")
    hits = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(404)
            self.end_headers()

        def log_message(self, *args):
            pass
    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    flat = f'''<?xml version="1.0" encoding="UTF-8"?>
<office:document xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" xmlns:xlink="http://www.w3.org/1999/xlink" xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0" office:version="1.3" office:mimetype="application/vnd.oasis.opendocument.text">
<office:body><office:text><text:p>Visible text</text:p>
<text:section text:name="S1"><text:section-source xlink:href="{secret.as_uri()}" xlink:type="simple" text:filter-name="Text"/><text:p>placeholder</text:p></text:section>
<text:p><draw:frame svg:width="2cm" svg:height="2cm" text:anchor-type="as-char"><draw:image xlink:href="http://127.0.0.1:{port}/pixel.png" xlink:type="simple" xlink:show="embed" xlink:actuate="onLoad"/></draw:frame></text:p>
</office:text></office:body></office:document>'''
    try:
        # Under each name: LibreOffice goes by the content, not the extension.
        for ext in ("odt", "pptx", "rtf"):
            source = tmp_path / f"links.{ext}"
            source.write_text(flat)
            for target in ("pdf", "txt"):
                result = _convert(source, ext, target, tmp_path)
                text = _pdf_text(result.path)[1] if target == "pdf" else result.path.read_text(errors="replace")
                assert "Visible text" in text and "CANARY" not in text, (ext, target)
        assert hits == []
    finally:
        server.shutdown()


@needs_office
def test_word_pages_as_pictures_come_from_libreoffice(tmp_path):
    assert office.handles("docx", "png") and office.handles("docx", "jpg")
    assert not office.handles("docx", "md") and not office.handles("docx", "html")


# ── the operator's accounts are not counted ──────────────────────────────────────────────────────────

def test_owner_accounts_are_left_out_of_taps_stats_and_usage(monkeypatch):
    sf = sys.modules[bot.track_activity.__module__]
    monkeypatch.setenv("ADMIN_ID", "111")
    monkeypatch.setenv("STATS_IGNORE_IDS", "222, 333")
    monkeypatch.setattr(sf, "_not_counted_ids", None)
    monkeypatch.setattr(sf.live_message, "note_update", lambda update: None)
    monkeypatch.setattr(sf, "remember_chat_lang", lambda *a: None)
    monkeypatch.setattr(sf.family_link, "_bot_id", "convertbot", raising=False)
    sf.take_taps(), sf.take_stats()
    assert sf.not_counted_ids() >= {111, 222, 333}

    def update(uid):
        return SimpleNamespace(effective_user=SimpleNamespace(id=uid), callback_query=SimpleNamespace(data="convfmt:pdf"))

    async def handled(uid):
        # One task for several updates, as python-telegram-bot does without concurrent updates.
        await sf.track_activity(update(uid), SimpleNamespace(user_data={}, application=None))
        sf.note_stat("conversion", "pptx>pdf")

    async def some_updates():
        before = sf._usage_updates
        for uid in (111, 222, 444, 333, 555):
            await handled(uid)
        return sf._usage_updates - before

    counted_updates = run(some_updates())
    assert counted_updates == 2
    assert sf.take_taps() == {"convfmt:pdf": 2}
    assert sf.take_stats() == {("conversion", "pptx>pdf"): 2}
    assert not ({111, 222, 333} & sf._usage_users) and {444, 555} <= sf._usage_users
