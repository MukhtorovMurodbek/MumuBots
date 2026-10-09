"""A stranger's file that is built to hurt is refused, with a sentence.

    python3 tests/test_hostile_files.py

Two things go wrong with a data or document file that is small on disk and
large in use, and both are found the same way: by sending one.

  It takes the machine. A YAML document of nine short lines, each a list of
  nine references to the line before, is nine to the ninth power values once
  it is written out as JSON -- under 500 bytes in, 573 MB and five seconds out
  at seven levels, and nine times that for every level more. A PDF whose page
  is written down as 9,000 points a side is 350 megapixels and a gigabyte to
  draw, in a file of 500 bytes. Neither is over any size limit, because the
  limits are on the file.

  It is called a fault. The worker turns a ConversionError into "refused" and
  anything else into "crash", and a crash is refunded *and* reported to the
  owner. A CSV with a cell over the csv module's 128 KB limit, a JSON file
  nested past the interpreter's recursion limit, and a YAML anchor that
  contains itself all raised something else -- so a person sending a bad file
  paged the owner about it.

So the rule is the same for every such file: it ends in a ConversionError that
says what is wrong with it, quickly, and never in anything else. The
legitimate neighbours of each -- anchors used the way config files use them, a
spreadsheet with blank formatted rows, a big page that is still a page -- are
checked to still convert, because a guard that refuses real files is the
other way to be wrong.

Needs PyYAML for the YAML checks, openpyxl for the spreadsheet one and PyMuPDF
for the pages; each is skipped, and says so, where it is missing.
"""
import os
import pathlib
import sys
import tempfile
import time
import tracemalloc

HERE = pathlib.Path(__file__).resolve().parent
MIRROR = HERE / ".mirror" / "convert_bot"
sys.path.insert(0, str(MIRROR if MIRROR.exists() else HERE.parent / "convert_bot"))
sys.path.insert(1, str(HERE.parent / "shared"))
sys.path.append(str(HERE / "stubs"))             # after site-packages: a real library beats a stub
os.environ.setdefault("CBOT_TOKEN", "test")
os.environ.setdefault("CBOT_USERNAME", "test")
os.environ.setdefault("DATABASE_URL", "postgresql://stub")
os.environ["ENV_FILES"] = "off"

import formats                                          # noqa: E402
import data_convert                                     # noqa: E402
from convert_utils import ConversionError               # noqa: E402

failures = []
WORK = pathlib.Path(tempfile.mkdtemp(prefix="hostile-files-"))

# A guard that works answers at once. Seconds, not the minutes the same file
# took to get a machine into trouble.
QUICK_S = 5.0


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL  ") + name + (f" -- {detail}" if not ok and detail else ""))
    if not ok:
        failures.append(name)


def have(module):
    try:
        __import__(module)
        return True
    except ImportError:
        return False


def convert(name, content, src, target):
    """(what happened, seconds): "ok", "refused: <sentence>", or "ESCAPED <type>"."""
    path = WORK / f"{name}.{src}"
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    started = time.monotonic()
    try:
        data_convert.convert(str(path), src, target, WORK, stem=f"{name}-out")
        outcome = "ok"
    except ConversionError as exc:
        outcome = f"refused: {exc}"
    except BaseException as exc:                        # noqa: BLE001 - that is the point
        outcome = f"ESCAPED {type(exc).__name__}: {str(exc)[:80]}"
    return outcome, time.monotonic() - started


def refused(name, content, src, target, mention=""):
    outcome, seconds = convert(name, content, src, target)
    check(f"{name}: refused with a sentence, in {seconds:.1f}s",
          outcome.startswith("refused:") and seconds < QUICK_S and mention in outcome,
          f"{outcome} ({seconds:.1f}s)")


def converts(name, content, src, target):
    outcome, seconds = convert(name, content, src, target)
    check(f"{name}: still converts", outcome == "ok", outcome)


def alias_bomb(levels):
    """`a0` is a list of nine values; each next line is nine references to the
    one before. Nine to the `levels`-th values, a few hundred bytes of file."""
    lines = ["a0: &a0 [x,x,x,x,x,x,x,x,x]"]
    for i in range(1, levels):
        lines.append(f"a{i}: &a{i} [" + ",".join([f"*a{i - 1}"] * 9) + "]")
    return "\n".join(lines) + "\n"


def main() -> int:
    print("=== a file that is nested past what can be walked ===")
    refused("json_deep", "[" * 100_000 + "]" * 100_000, "json", "yaml", "nested too deeply")
    if have("defusedxml"):
        refused("xml_deep", "<a>" * 5_000 + "</a>" * 5_000, "xml", "json", "nested too deeply")
    else:
        print("  skipped  xml_deep: defusedxml is not installed")
    if have("yaml"):
        converts("json_nested_200", "[" * 200 + "]" * 200, "json", "yaml")

    print("\n=== a CSV the csv module will not read ===")
    refused("csv_long_cell", "a,b\n" + "x" * 200_000 + ",1\n", "csv", "json", "isn't a CSV")

    print("\n=== a table is refused at its limit, not after it is all in memory ===")
    # Three million blank lines: the old code read every one into a list before
    # asking whether that was too many.
    refused("csv_blank_rows", "a,b\n" + "\n" * 3_000_000, "csv", "json", "row limit")
    # Refused, and without having held them: the old answer was the same
    # sentence, given after 200 MB of empty lists.
    tracemalloc.start()
    try:
        data_convert.convert(str(WORK / "csv_blank_rows.csv"), "csv", "json", WORK, stem="blank-out")
    except ConversionError:
        pass
    peak_mb = tracemalloc.get_traced_memory()[1] / 1e6
    tracemalloc.stop()
    check("...and never held the blank lines it was refusing", peak_mb < 60, f"peak {peak_mb:.0f} MB")
    # The cell limit counted the header's width only, so 5,000 rows of 900
    # columns under a one-column header came to "5,000 cells" and went through.
    refused("csv_wide", "a\n" + ("," * 900 + "\n") * 5_000, "csv", "json", "cell limit")
    converts("csv_ordinary", "name,age\nAda,36\nAlan,41\n", "csv", "json")

    print("\n=== YAML that repeats itself ===")
    if have("yaml"):
        # Seven levels and no more, on purpose: it is the size that takes half a
        # gigabyte and five seconds to write out when nothing guards it, so a
        # regression here is a failure, not a machine that has run out of memory
        # (at twenty levels it would be).
        refused("yaml_bomb_7", alias_bomb(7), "yaml", "json", "anchors and aliases")
        # An anchor that contains its own alias never ends: json.dumps says
        # "Circular reference detected", which is not a ConversionError.
        refused("yaml_loop", "a: &a\n  - *a\n", "yaml", "json", "anchors and aliases")
        # ...and the ordinary use of anchors, the one every config file makes,
        # is not what is refused.
        converts("yaml_config_anchors",
                 "base: &b {retries: 3, timeout: 30}\nfirst:\n  <<: *b\n  name: one\n"
                 "second:\n  <<: *b\n  name: two\n", "yaml", "json")
        converts("yaml_small_bomb", alias_bomb(5), "yaml", "json")     # 59,049 values
    else:
        print("  skipped  the YAML checks: PyYAML is not installed")

    print("\n=== a spreadsheet Excel says is a million rows ===")
    if have("openpyxl"):
        import openpyxl
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.append(["h1", "h2"])
        sheet.append([1, 2])
        sheet.append([3, 4])
        sheet.cell(row=200_000, column=1).number_format = "0.00"       # formatted, empty
        path = WORK / "phantom.xlsx"
        book.save(path)
        started = time.monotonic()
        try:
            data_convert.convert(str(path), "xlsx", "csv", WORK, stem="phantom-out")
            outcome = "ok"
        except ConversionError as exc:
            outcome = f"refused: {exc}"
        check("a sheet with 200,000 formatted blank rows and two real ones converts",
              outcome == "ok", outcome)
        check("...without reading them all into memory first",
              time.monotonic() - started < QUICK_S)
    else:
        print("  skipped  the spreadsheet check: openpyxl is not installed")

    print("\n=== a page that is too big to draw ===")
    if have("pymupdf"):
        import pymupdf
        import doc_convert

        def pdf_with_pages(*sizes):
            doc = pymupdf.open()
            for width, height in sizes:
                doc.new_page(width=width, height=height)
            path = WORK / f"pages-{len(list(WORK.iterdir()))}.pdf"
            doc.save(str(path))
            doc.close()
            return path

        def render(path):
            started = time.monotonic()
            try:
                doc_convert.convert(str(path), "pdf", "png", WORK, stem=f"{path.stem}-out")
                return "ok", time.monotonic() - started
            except ConversionError as exc:
                return f"refused: {exc}", time.monotonic() - started
            except BaseException as exc:                # noqa: BLE001
                return f"ESCAPED {type(exc).__name__}: {str(exc)[:80]}", time.monotonic() - started

        # 9,000 points is 125 inches: 352 megapixels, and a gigabyte of pixmap.
        # MuPDF draws it without complaint (it refuses only from about 9,500),
        # so it is this guard or nothing.
        outcome, seconds = render(pdf_with_pages((9_000, 9_000)))
        check("a 125-inch square page is refused, quickly, without drawing it",
              outcome.startswith("refused:") and "megapixel" in outcome and seconds < QUICK_S,
              f"{outcome} ({seconds:.1f}s)")
        # The poster on the last page is found before anything is drawn, not
        # after every page ahead of it.
        outcome, seconds = render(pdf_with_pages((595, 842), (595, 842), (9_000, 9_000)))
        check("a huge page after two ordinary ones is refused all the same",
              outcome.startswith("refused:") and "Page 3" in outcome, outcome)
        outcome, _ = render(pdf_with_pages((595, 842)))
        check("an A4 page still renders", outcome == "ok", outcome)
        # A0 is 2384 x 3370 points: 35 megapixels at 150 dpi, a real thing that
        # people print. It must not be what the limit catches.
        outcome, _ = render(pdf_with_pages((2384, 3370)))
        check("an A0 poster still renders", outcome == "ok", outcome)
        limit = getattr(formats, "MAX_PAGE_MEGAPIXELS", 0)
        check("the limit is above an A0 poster and below what would hurt",
              35 < limit < 250, str(limit))
    else:
        print("  skipped  the page checks: PyMuPDF is not installed")

    print()
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for name in failures:
            print("  -", name)
        return 1
    print("a file built to hurt is refused, and the ordinary ones convert.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
