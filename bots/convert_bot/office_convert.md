# `office_convert.py`

[↑ convert_bot](README.md) · [code](office_convert.py)

Office files -- PowerPoint, Word, Excel and their OpenDocument twins -- through LibreOffice, run headless, one fresh process and one fresh profile per conversion.

What LibreOffice writes directly: a deck as PPTX, PPT, ODP or PDF; a text document as DOCX, DOC, ODT, RTF or PDF; a spreadsheet as XLSX, XLS, ODS or PDF; and a PDF as an editable PPTX or ODP (its PDF import makes text boxes and shapes, not pictures). Everything else goes through one of those: a deck's pictures and text come from its PDF (doc_convert), an old spreadsheet's CSV or JSON from its XLSX (data_convert).

_At `_DIRECT = {`:_

What LibreOffice writes itself, by the kind of file it opened.

_At `ONLY_HERE = ("pptx", "ppt", "odp", "doc", "odt", "rtf", "xls", "ods")`:_

Targets only LibreOffice writes: any conversion into one of these comes here.

_At `_FILTERS = {`:_

The export filter by name where the bare extension would pick a less compatible one.

_At `_RESTART = 81`:_

Exit status of a first start on a new profile: it was made, start again.

_At `_PROFILE_SETTINGS = """<?xml version="1.0" encoding="UTF-8"?>`:_

A new profile with macros off and nothing fetched from outside: whatever arrives in a
document stays inert. Written before the first start, so it is in force from the start.

### `soffice_path()`

LibreOffice's own program, soffice.bin, where it can be found: started directly it is one process, which a timeout can stop, where the `soffice` launcher would leave it running.

### `has_pdf_import()`

Whether this LibreOffice can open a PDF as slides (its pdfimport library is a separate part on some systems).

### `handles()`

Whether this conversion is LibreOffice's to do.

- At `return src_ext == "docx" and target_ext in ("pdf", "png", "jpg") and formats.met("office")`:
  A Word document's PDF -- and its pages as pictures -- keep the layout only when Word's own
  rules lay it out; without LibreOffice doc_convert re-flows it.

### `_run()`

- At `env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(profile),`:
  Only what it needs: none of the bots' tokens or database addresses reach a program that is
  opening somebody else's document.

### `soffice()`

`in_path` written as `target_ext` by LibreOffice; the path of what it wrote, inside work_dir.

- At `source = inbox / f"source.{src_ext}"`:
  A plain name: the result is named after the source, and a user's file name stays out of argv.
- At `raise ConversionError(`:
  LibreOffice says nothing and exits 0 when it can't open a file at all.

### `convert()`

- At `import doc_convert`:
  Pictures, text and HTML of a deck or a document come from its PDF.

### `selftest()`

A two-page PDF opened as slides, saved as PPTX, then as PDF again, and its text read back: main.py --check runs it, so an image whose LibreOffice can't do the conversions offered fails the build. SystemExit on failure.

- At `(folder / "check.html").write_text("<p>Office check document</p>", encoding="utf-8")`:
  Writer and Calc are packages of their own: one document and one spreadsheet each way.
