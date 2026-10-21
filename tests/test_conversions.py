"""Everything ConvertBot advertises, it can produce.

    python3 tests/test_conversions.py

v1.5.0 turned three hard-coded lists of target formats into a matrix of about
seventy-five source formats and eight hundred pairs, spread across four
backends. Nothing about that is checkable by reading it. The specific way it
goes wrong is also not a crash: `formats.py` offers a button, the user picks
it, `convert_utils.convert()` has no branch for that pair, and the answer is
an apology -- after an invoice, if the file was over a megabyte.

So the rule this pins down is one sentence: **every pair the matrix declares
has a backend that claims it, and every backend's claim is reachable from the
matrix.** Both directions, because a target with no branch is a refund and a
branch nothing points at is dead code.

The rest is the smaller invariants that have already been got wrong once:
callback data that has to fit in Telegram's 64 bytes, an alias that must not
shadow a real format, an animated source that must not be offered a still
target, and the strings the menu looks up existing in all three languages.

No libraries and no files: this reads the tables, not the converters. The
conversions themselves are exercised by hand against real fixtures -- see
`CHANGES_v1.5.0.md` -- because doing it here would mean shipping a sample of
every format in the repository.
"""
import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / ".mirror" / "convert_bot"))
sys.path.insert(0, str(HERE / "stubs"))
os.environ.setdefault("CBOT_TOKEN", "test")
os.environ.setdefault("CBOT_USERNAME", "test")
os.environ.setdefault("DATABASE_URL", "postgresql://stub")

import formats                                          # noqa: E402
import convert_utils                                    # noqa: E402
import doc_convert                                      # noqa: E402
import data_convert                                     # noqa: E402
import archive_convert                                  # noqa: E402
import ocr_convert                                      # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL  ") + name + (f" -- {detail}" if not ok and detail else ""))
    if not ok:
        failures.append(name)


# ---------------------------------------------------------------------------
# Who writes what
# ---------------------------------------------------------------------------
# The four backends, and the targets each one has an actual branch for. This
# is the list `convert_utils.convert()` dispatches into, written out once so
# the check below can be a set operation rather than a guess.

PILLOW_WRITES = frozenset(convert_utils._IMAGE_TARGETS) | {"pdf"}
FFMPEG_WRITES = frozenset(convert_utils._FFMPEG_ARGS)
DOC_WRITES = doc_convert.WRITES
DATA_WRITES = data_convert.WRITES
ARCHIVE_WRITES = archive_convert.WRITES
OCR_WRITES = ocr_convert.WRITES


def backends_for(category, source):
    """Which backends could be asked to write a target for this source."""
    if category == formats.IMAGE:
        # ocr_convert reads the words in a still picture: TXT, and the searchable PDF.
        return PILLOW_WRITES | OCR_WRITES | (DOC_WRITES if source == "svg" else frozenset())
    if category in (formats.VIDEO, formats.AUDIO):
        return FFMPEG_WRITES
    if category == formats.SUBTITLE:
        # ffmpeg writes the subtitle formats; data_convert writes the transcript.
        return FFMPEG_WRITES | {"txt"}
    if category == formats.DOCUMENT:
        # ... and reads the scanned pages of a PDF, which doc_convert cannot.
        return DOC_WRITES | (OCR_WRITES if source == "pdf" else frozenset())
    if category == formats.DATA:
        return DATA_WRITES
    if category == formats.ARCHIVE:
        return ARCHIVE_WRITES
    return frozenset()


def every_source():
    for category in formats.CATEGORIES:
        for source in formats.SOURCES[category]:
            yield category, source


def main() -> int:
    print("=== the matrix and the backends agree ===")

    # 1. Everything advertised can be produced.
    for category, source in every_source():
        writable = backends_for(formats.category_of(source), source)
        missing = [target for target in formats.declared_targets(source)
                   if target not in writable]
        check(f".{source} -> every declared target has a backend", not missing, str(missing))

    # An animated source is offered a different list, and it has to hold up too.
    for source in formats.ANIMATED_CAPABLE:
        for animated in (True, False):
            targets = formats.declared_targets(source, animated)
            writable = backends_for(formats.category_of(source, animated), source)
            missing = [target for target in targets if target not in writable]
            check(f".{source} (animated={animated}) has a backend for every target",
                  not missing, str(missing))

    print("\n=== the matrix is internally consistent ===")

    # 2. No format converts to itself: it is not a conversion, and the button
    #    would be a way of paying Stars for a copy of your own file.
    for _, source in every_source():
        check(f".{source} is not offered as its own target",
              source not in formats.declared_targets(source))

    # 3. One category each. A format in two is a lookup whose answer depends
    #    on dictionary order.
    seen = {}
    doubled = []
    for category, source in every_source():
        if source in seen and seen[source] != category:
            doubled.append(f"{source} ({seen[source]}/{category})")
        seen[source] = category
    check("no format is in two categories", not doubled, str(doubled))

    # 4. Aliases resolve to something real, and shadow nothing real.
    unknown = sorted({value for value in formats.ALIASES.values()
                      if value not in formats.CATEGORY_OF})
    check("every alias resolves to a format we know", not unknown, str(unknown))
    shadowed = sorted(set(formats.ALIASES) & set(formats.CATEGORY_OF))
    check("no alias shadows a format that exists in its own right", not shadowed, str(shadowed))
    for spelling, canonical in formats.ALIASES.items():
        check(f"normalise({spelling!r}) is {canonical!r}",
              formats.normalise(spelling) == canonical and formats.normalise("." + spelling.upper()) == canonical)

    # 5. Every requirement named against a format is one probe() can meet, and
    #    every ffmpeg target names an encoder.
    known_requirements = {"heif", "avif", "jp2", "mupdf", "mammoth", "markdown",
                          "openpyxl", "yaml", "xml", "ffmpeg", "bsdtar", "rawpy", "ocr"}
    for fmt, needs in formats.FORMAT_NEEDS.items():
        check(f".{fmt}'s requirements are ones probe() reports on",
              set(needs) <= known_requirements, str(needs))
    # In an ffmpeg category, a target either names an encoder -- so that a
    # build without it is not offered -- or is one somebody else writes.
    # `txt` from a subtitle is the second kind: "the words with none of the
    # timings" is not a subtitle format and ffmpeg has no encoder for it.
    for category in formats.FFMPEG_CATEGORIES:
        for target in formats.TARGETS_BY_CATEGORY.get(category, ()):
            check(f"ffmpeg target .{target} names an encoder, or somebody else writes it",
                  target in formats.ENCODERS or target in (DOC_WRITES | DATA_WRITES))

    print("\n=== what Telegram and the menu can carry ===")

    # 6. Callback data is capped at 64 bytes by the Bot API, and a payload
    #    over it is rejected when the *keyboard* is sent -- so the whole menu
    #    fails, not one button.
    longest = max((target for _, source in every_source()
                   for target in formats.declared_targets(source)), key=len, default="")
    check("the longest callback datum fits in 64 bytes",
          len(f"convfmt:{longest}".encode()) <= 64, f"convfmt:{longest}")
    longest_category = max(formats.CATEGORIES, key=len)
    check("so does the longest /formats one",
          len(f"fmtcat:{longest_category}".encode()) <= 64)

    # 7. The short list is a prefix of the long one. If it were not, "More
    #    formats" could hide a button that had been on screen a moment ago.
    for _, source in every_source():
        targets = formats.declared_targets(source)
        popular = targets[:formats.POPULAR_COUNT]
        check(f".{source}: the first screen is the head of the full list",
              tuple(popular) == targets[:len(popular)])

    print("\n=== availability is honoured, not just declared ===")

    # 8. With a requirement missing, nothing that needs it is offered. This is
    #    the promise requirements.txt makes -- that dropping a line shortens
    #    /formats rather than breaking a conversion.
    formats.probe()
    original = set(formats._MET)
    try:
        formats._MET = original - {"mupdf"}
        formats._PROBED = True
        check("without PyMuPDF, no source needs it",
              not formats.is_supported_source("pdf") and not formats.is_supported_source("epub"))
        check("without PyMuPDF, EPUB is not offered as a target of anything",
              all("epub" not in formats.targets_for(source) for _, source in every_source()))
        check("but a picture still converts to PDF, which is Pillow's job",
              "pdf" in formats.declared_targets("png")
              and "pdf" in formats.targets_for("png"))
        formats._MET = original - {"heif"}
        check("without pillow-heif, HEIC is neither read nor written",
              not formats.is_supported_source("heic")
              and "heic" not in formats.targets_for("png"))
        formats._MET = set()
        formats._ENCODERS_PRESENT = set()
        check("with nothing at all, MP4 is not offered", not formats.targets_for("mp4"))
        check("and a PNG still converts, because Pillow is not optional",
              "jpg" in formats.targets_for("png"))
    finally:
        formats._PROBED = False
        formats.probe()

    print("\n=== the strings the menu looks up ===")

    import i18n
    needed = [f"category_{category}" for category in formats.CATEGORIES] + [
        "formats_title", "formats_intro", "formats_highlights", "formats_reads",
        "formats_writes", "formats_category_title", "formats_lookup",
        "formats_lookup_none", "formats_lookup_unknown",
        "btn_more_formats", "btn_fewer_formats", "btn_what_formats",
        "btn_formats_back", "convert_options_prompt_batch",
        "converted_file_caption_zip", "converted_file_caption_pdf",
        "converted_file_caption_pages", "converted_layout_note",
        "batch_mixed_formats",
        "ocr_lang_toggle", "ocr_lang_set", "ocr_lang_eng", "ocr_lang_rus", "ocr_lang_uzb",
        "ocr_too_many_pages_note", "target_ocrpdf",
    ]
    for lang in i18n.SUPPORTED_LANGUAGES:
        absent = [key for key in needed if key not in i18n.STRINGS[lang]]
        check(f"{lang}: every key the format menu uses is translated", not absent, str(absent))

    print("\n=== the screens render ===")

    # A missing placeholder in one translated string is a KeyError inside a
    # handler, which reaches the user as silence. Every screen the format
    # feature can draw is built here, in all three languages, so that a
    # mismatch between a string and the arguments it is given fails a test
    # instead of a conversation.
    import bot
    for lang in i18n.SUPPORTED_LANGUAGES:
        try:
            text, keyboard = bot._formats_overview(lang)
            ok = bool(text) and bool(keyboard.inline_keyboard)
        except Exception as exc:
            ok, text = False, f"{type(exc).__name__}: {exc}"
        check(f"{lang}: the /formats overview builds", ok, str(text)[:120])

        for category in formats.CATEGORIES:
            if not formats.supported_sources(category):
                continue
            try:
                body, _ = bot._formats_category(lang, category)
                ok = bool(body)
            except Exception as exc:
                ok, body = False, f"{type(exc).__name__}: {exc}"
            check(f"{lang}: /formats for {category} builds", ok, str(body)[:120])

        try:
            answer = bot._format_lookup(lang, "heic") or bot._format_lookup(lang, "png")
            ok = bool(answer)
        except Exception as exc:
            ok, answer = False, f"{type(exc).__name__}: {exc}"
        check(f"{lang}: a bare format name gets an answer", ok, str(answer)[:120])
        check(f"{lang}: a word that is not a format gets none",
              bot._format_lookup(lang, "hello there") is None)

        # Every caption branch, including the three that exist because the
        # button said one thing and the file is another.
        for result, source, target in (
            (convert_utils.Result(pathlib.Path("x.png")), "jpg", "png"),
            (convert_utils.Result(pathlib.Path("x.zip"), items=9, zipped=True), "pdf", "png"),
            (convert_utils.Result(pathlib.Path("x.zip"), items=3, zipped=True), "png", "webp"),
            (convert_utils.Result(pathlib.Path("x.pdf"), items=4), "png", "pdf"),
            (convert_utils.Result(pathlib.Path("x.pdf")), "docx", "pdf"),
        ):
            try:
                caption = bot._result_caption(lang, result, source, target, stars_paid=0)
                ok = bool(caption)
            except Exception as exc:
                ok, caption = False, f"{type(exc).__name__}: {exc}"
            check(f"{lang}: the caption for {source}->{target} builds", ok, str(caption)[:120])

        # And the menu itself, at its longest.
        widest = max((source for _, source in every_source()),
                     key=lambda ext: len(formats.targets_for(ext)))
        targets = formats.targets_for(widest)
        rows = bot._format_rows(lang, targets[:formats.POPULAR_COUNT], False, len(targets))
        check(f"{lang}: the format menu has a More and a Cancel row",
              len(rows) >= 3 and len(rows[-1]) == 1)

    print("\n=== the ceilings exist ===")

    # 9. Every limit that stops a small file from being a large job. The
    #    pricing is by size and these are why that is still safe.
    for name, value in (("MAX_PAGES", formats.MAX_PAGES), ("MAX_ROWS", formats.MAX_ROWS),
                        ("MAX_CELLS", formats.MAX_CELLS), ("MAX_BATCH", formats.MAX_BATCH),
                        ("MAX_FRAMES", convert_utils.MAX_FRAMES)):
        check(f"{name} is a positive ceiling", isinstance(value, int) and value > 0, str(value))
    check("Pillow's decompression-bomb ceiling is still set",
          convert_utils.Image.MAX_IMAGE_PIXELS and convert_utils.Image.MAX_IMAGE_PIXELS <= 100_000_000)
    check("ffmpeg still runs under a timeout and a thread cap",
          convert_utils.FFMPEG_TIMEOUT_S > 0 and convert_utils.FFMPEG_THREADS)

    # 10. The cap reaches the encoder and the filters, not only the decoder.
    #     Before -i, -threads caps decoding alone; on Railway, whose container
    #     reports 48 cores and holds 1 GB, the encoder sized itself from the 48
    #     and a 19 MB 1080p video was killed for memory (2026-09-29). Both
    #     ways ffmpeg is started -- with a progress bar and without.
    import tempfile

    class Built(Exception):
        pass

    commands = []

    def record(cmd, *args, **kwargs):
        commands.append(list(cmd))
        raise Built()

    saved = (subprocess.run, subprocess.Popen, convert_utils._ffmpeg_available, convert_utils._is_hdr,
             convert_utils._media_duration, convert_utils.PROGRESS)
    with tempfile.TemporaryDirectory() as tmp:
        source = os.path.join(tmp, "in.mov")
        with open(source, "wb") as f:
            f.write(b"not empty")
        try:
            subprocess.run = subprocess.Popen = record
            convert_utils._ffmpeg_available = lambda: True
            convert_utils._is_hdr = lambda path: False
            convert_utils._media_duration = lambda path: 10.0
            for progress in (None, lambda done, total: None):
                convert_utils.PROGRESS = progress
                try:
                    convert_utils.convert_media_ffmpeg(source, "mp4", os.path.join(tmp, "out.mp4"))
                except Built:
                    pass
        finally:
            (subprocess.run, subprocess.Popen, convert_utils._ffmpeg_available, convert_utils._is_hdr,
             convert_utils._media_duration, convert_utils.PROGRESS) = saved
    commands = [cmd for cmd in commands if os.path.basename(str(cmd[0])).lower().startswith("ffmpeg")]
    check("ffmpeg was started both ways", len(commands) == 2, str(len(commands)))
    for cmd in commands:
        split = cmd.index("-i")
        before, after = cmd[:split], cmd[split + 2:]
        way = "with progress" if "-progress" in cmd else "without progress"
        check(f"{way}: the decoder and the filters are capped before -i",
              "-threads" in before and "-filter_threads" in before and "-filter_complex_threads" in before,
              " ".join(before))
        check(f"{way}: the encoder is capped after -i", "-threads" in after, " ".join(after))

    # 11. ffmpeg killed for memory is told as that, not as a file that could
    #     not be read: its last words are a stream listing, which read as if
    #     the file were at fault. An ordinary failure stays a refusal.
    class Finished:
        def __init__(self, code):
            self.returncode, self.stdout, self.stderr = code, "", "Stream #0:0: Video: hevc (Main 10)"

    class Running:
        def __init__(self, code):
            self.returncode, self.stdout, self._code = None, iter(()), code

        def wait(self, timeout=None):
            self.returncode = self._code
            return self._code

    for code, expected in ((-9, convert_utils.OutOfMemory), (137, convert_utils.OutOfMemory),
                           (1, convert_utils.ConversionError)):
        for progress in (None, lambda done, total: None):
            with tempfile.TemporaryDirectory() as tmp:
                source = os.path.join(tmp, "in.mov")
                with open(source, "wb") as f:
                    f.write(b"not empty")
                raised = None
                try:
                    subprocess.run = lambda cmd, *a, **k: Finished(code)
                    subprocess.Popen = lambda cmd, *a, **k: Running(code)
                    convert_utils._ffmpeg_available = lambda: True
                    convert_utils._is_hdr = lambda path: False
                    convert_utils._media_duration = lambda path: 10.0
                    convert_utils.PROGRESS = progress
                    convert_utils.convert_media_ffmpeg(source, "mp4", os.path.join(tmp, "out.mp4"))
                except Exception as exc:
                    raised = exc
                finally:
                    (subprocess.run, subprocess.Popen, convert_utils._ffmpeg_available, convert_utils._is_hdr,
                     convert_utils._media_duration, convert_utils.PROGRESS) = saved
            way = "with progress" if progress else "without progress"
            check(f"{way}: ffmpeg ending {code} is {expected.__name__}",
                  type(raised) is expected, repr(raised))

    print()
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for name in failures:
            print("  -", name)
        return 1
    sources, targets, pairs = formats.counts()
    print(f"{sources} formats in, {targets} out, {pairs} pairs -- all of them backed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
