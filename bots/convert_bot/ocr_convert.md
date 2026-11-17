# `ocr_convert.py`

[↑ convert_bot](README.md) · [code](ocr_convert.py)

Text from pictures and scanned PDFs, recognised on this machine.

Four conversions: a picture to TXT, a picture to a searchable PDF ("ocrpdf",
a pseudo-target whose file is `<stem>.pdf`), a PDF to TXT and a PDF to a
searchable PDF. The engine is Tesseract, and it is already in the bot: MuPDF
links it, so PyMuPDF can write a page with an invisible text layer
(`Pixmap.pdfocr_tobytes`). There is no `tesseract` binary and no pytesseract;
the only thing added to the image is the language data (`tessdata_dir`).
Nothing is sent anywhere: the text is read on the machine that holds the file.

A page counts as "scanned" when its own text is shorter than
OCR_MIN_TEXT_CHARS (`is_scanned`). The same predicate prices the job
(`count_scanned`, which the bot asks in a process of its own, as it does for a
RAW photo) and does the work, so the price never counts a page that will not
be read and the worker never reads one the price did not count. A page with
text keeps its own: TXT takes it as it is, the searchable PDF leaves the page
alone.

\## What it costs, measured 2026-10-08/09 (PyMuPDF 1.28.2, the laptop)

```text
time      1.1-2.0 s a page at 300 dpi; production is estimated 1.5-2.5x slower.
          The worst seen was 94 s of CPU on a 12 MP image of pure noise, so a
          page that runs away is the worker's time limit's job (jobs.py), not
          this module's.
memory    17-19 MB per megapixel of the picture handed to the engine: a 48 MP
          photograph killed the container. So no picture reaches it over
          OCR_MAX_PIXELS: a page is drawn at the dpi that keeps it under
          (`ocr_dpi`), and a photograph is brought down while it is decoded
          (`_picture`). MuPDF's own cache is the other half: it grew 26 MB a
          page, up to 256 MB, until it was shrunk after every page.
size      the text layer is laid over the page that was there (rather than
          the page being replaced by the engine's), +5 KB a page. The page
          keeps its size, rotation and picture and renders pixel for pixel
          as before at 0, 90, 180 and 270 degrees.
```

_At `OCR_MAX_PIXELS = int(os.environ.get("CONVERT_OCR_MAX_PIXELS") or 12_000_000)`:_

A picture handed to the engine is never over this many pixels.

_At `OCR_PAGE_S = int(os.environ.get("CONVERT_OCR_PAGE_SECONDS") or 15)`:_

Seconds a page is allowed to take, which is what jobs.ocr_time_limit() and so
the price are made of: measured 1.1-2.0 s on the laptop, production perhaps
2.5x that, so about three times what is expected.

_At `OCR_MAX_PAGES = int(os.environ.get("CONVERT_OCR_MAX_PAGES") or 30)`:_

The most scanned pages one job reads; the menu does not offer past it.

_At `OCR_DESCRIBE_SECONDS = float(os.environ.get("CONVERT_OCR_DESCRIBE_SECONDS") or 5)`:_

The most the menu waits, in total, for a PDF's pages to be counted
(jobs.describe_pdf). The bot handles one update at a time and the menu is
drawn inside that handler, so a stranger's PDF that takes the counting
minutes (a page of a million glyphs is 1.8 KB and took 17 s) would hold every
other person's buttons for as long. Past this the child is killed, and the
menu is drawn with the page count and no scanned pages: today's TXT, no OCR.

_At `OCR_FREE_PAGES = int(os.environ.get("CONVERT_OCR_FREE_PAGES") or 1)`:_

Scanned pages a job may have and still be free (when its size is, too).

_At `OCR_MIN_TEXT_CHARS = int(os.environ.get("CONVERT_OCR_MIN_TEXT_CHARS") or 20)`:_

A page whose own text is shorter than this (whitespace left out) is a picture
of one. Not zero: a scan often carries a stamp or a page number as real text.

_At `OCR_MIN_DPI = int(os.environ.get("CONVERT_OCR_MIN_DPI") or 50)`:_

Below this the engine reads garbage. A page that would need fewer dpi to stay
under OCR_MAX_PIXELS (a poster, a drawing: past about 5000 pt a side) is not
recognised, and so not counted either.

_At `OCR_MAX_DECODE_PIXELS = int(os.environ.get("CONVERT_OCR_MAX_DECODE_PIXELS") or 64_000_000)`:_

A picture that is not a JPEG is decoded whole, so past this many pixels it is
refused before anything is decoded (a JPEG is decoded at a fraction of its
size -- see \_picture -- and is judged by what it is decoded to).

_At `LANGS = {"eng": "eng", "rus": "rus", "uzb": "uzb+uzb_cyrl"}`:_

The person's choice (user_data["ocr_lang"]) -> what Tesseract is told. Uzbek is
written in two scripts and one pass reads both, at no measurable extra cost.
The order is the order the toggle cycles in.

_At `DEFAULT_CHOICE = {"en": "eng", "ru": "rus", "uz": "uzb"}`:_

What a person who has not chosen is given: the language they use the bot in.

_At `_TRAINEDDATA = ("eng", "rus", "uzb", "uzb_cyrl")`:_

What has to be on disk for all of them.

### `tesseract_language()`

The language string for a choice. Anything unknown or empty is English:
a job queued by an older build, or a hand-edited row, still runs.

### `default_choice()`

The text language a person gets before they have chosen one.

### `tessdata_dir()`

TESSDATA_PREFIX when it holds all four language files, else None.

### `available()`

- At `import importlib.metadata`:
  Read from the installed version, not by importing it: since PyMuPDF 2.0 the import also
  loads pymupdf4llm and onnxruntime, some 90 MB, into a bot that may never see a document.

## Which pages, and at what size

### `ocr_dpi()`

The dpi to draw a page of this size at for the engine: 300 where that
stays under OCR_MAX_PIXELS, less where it does not (an A0 poster comes out
at 87 or so). None when that would be under OCR_MIN_DPI, and for a page
with no size (zero, negative, NaN): it is not recognised.

The drawing is rounded out to whole pixels on each side, so one pixel more
a side is allowed for -- without it a picture exactly at the limit came
out a few thousand pixels over it.

### `is_scanned()`

A page that is a picture of text: there is a picture on it, it has too
little text of its own, and it can be drawn at a size the engine can read.
The one predicate the count (and so the price) and the conversion both use.

The picture comes first. A page with none has nothing to read -- a blank
separator page, a title slide, a drawn diagram -- and counting those priced
an ordinary report with two blank pages as a text-recognition job and
refused a deck of title slides (reviewed 2026-10-10). It is also the cheap
test: the page's list of pictures is read from its resources, where the text
is laid out from its whole content.

### `count_scanned()`

{"pages": n, "scanned": m} for a PDF: its page count, and how many of
its first formats.MAX_PAGES pages are scanned. Read here, in a process of
its own (jobs.describe_pdf), because MuPDF reading a stranger's file is not
something the bot's own process should do. Raises ConversionError for a file
that is not a readable PDF.

That process is on a deadline (OCR_DESCRIBE_SECONDS), and looking at the
pages can be slow where counting them is not, so the answer is given in
two steps. `on_pages(n)` is called with the page count as soon as the file
is open, before any page is looked at. `scan=False` looks at none: the
answer is {"pages": n, "scanned": None} (a host that cannot read text has
no use for the number). `stop_after` ends the looking once more than that
many pages are scanned, with `stop_after + 1` as the count: nothing needs
the exact number past the most a conversion reads.

## One page

### `_recognise()`

The text on one scanned page. With `layer`, the page also gets an
invisible copy of it, laid over what was there.

The engine makes a PDF page of its own holding the picture it was given and
the text; the picture is deleted from it (kept, it doubled the file), and
what is left is laid over the original page. The page keeps its size and
rotation, so it is not replaced and nothing about it moves.

- `if dpi is None:` — callers have asked is_scanned(); this is the backstop
- At `rotation = page.rotation`:
  The layer was drawn from the page as shown, so on a turned page
  (/Rotate 90, 180, 270) it is turned back to sit on the page as
  stored. Laid on as it is, the words ran sideways beside the
  picture and everything past the stored width was cut off, and
  nothing in how the page looks gives that away (found 2026-10-09).
- At `pymupdf.TOOLS.store_shrink(100)`:
  MuPDF keeps what it decoded and laid out, and without this it held
  on to 26 MB more with every page, up to 256 MB.

## A PDF

### `_pdf()`

- `pages = doc_convert._page_count(doc)` — the limit every PDF conversion has
- At `raise ConversionError(`:
  The job was priced for max_scanned pages; the menu counted them with
  this same predicate, so this is the file changing, not a rounding.
- `texts = []` — for TXT: one entry per page, in order
- At `for index in (range(pages) if target_ext == "txt" else scanned):`:
  A searchable PDF only touches the scanned pages; TXT goes through all of them.
- `text = page.get_text()` — a page with its own text (TXT only)
- At `out_path.write_text("\n".join(texts), encoding="utf-8")`:
  Pages joined as a PDF's text is everywhere else in this bot
  (doc_convert.\_to_text): each page's own newline, then one more.

## A picture

_At `_GREY_16_TO_8 = [value >> 8 for value in range(65536)]`:_

16-bit grey to 8: the high byte, which is what dividing by 256 gave. The one
lookup table the 32-bit "I" mode is taken to "L" with (Pillow reads 65536
entries for that; an index outside them is clipped to the first or the last).

_At `_GREY_BAND_PIXELS = 1_000_000`:_

Rows of a 16-bit picture turned to grey at a time: about a megapixel, so the
32-bit copy that conversion needs is 4 MB and not four bytes a pixel of the
whole picture (a 64 MP scan peaked near 640 MB when it was made whole).

### `_eight_bit()`

The picture as L, RGB or RGBA, which is what a resize and a JPEG can
take. A 1-bit fax scan and a 16-bit grey scan become grey; the rest RGB,
keeping transparency until it is flattened.

- At `return im.point(_GREY_16_TO_8, "L")`:
  16-bit grey: Pillow's own conversion would clip everything over 255
  to white. One lookup table, straight to 8 bits.
- At `width, height = im.size`:
  The same, a band of rows at a time: the table is for the 32-bit mode,
  and turning a whole 16-bit picture into that first is the 640 MB.

### `_tiff_pages()`

How many pages (directories) an opened TIFF holds, counted from the file's
headers and without decoding a pixel. 1 when that cannot be told: the decode
that follows reports a file that is damaged.

- `return 1` — EOFError for the one page; anything else is the decode's to report

### `_picture()`

A picture ready to be a PDF page: (bytes, format name, width, height).

A big JPEG is decoded at a fraction of its size (Pillow's `draft`, which
makes libjpeg scale while it decodes), so the full photograph is never in
memory. Anything else is decoded whole and resized, which is why one over
OCR_MAX_DECODE_PIXELS is refused first. The picture comes out at most
OCR_MAX_PIXELS, the right way up, on white where it was see-through.

- At `raise ConversionError(`:
  Pillow decodes the first and says nothing of the rest, so the
  text of every other page was dropped without a word. A scan of a
  document is one page each in a PDF; say so rather than guess.
- At `if im.size[0] * im.size[1] > OCR_MAX_DECODE_PIXELS:`:
  Judged by what is about to be decoded: after the draft for a JPEG,
  the whole picture for anything else.
- At `orientation = 1`:
  Asked after the ceiling above, not before it: a PNG's EXIF can follow
  its pixels, so Pillow decodes the whole picture to find it.

### `_image()`

- At `doc = pymupdf.open()`:
  One page the size of the picture at 300 dpi, holding the picture: from
  here it is a scanned page like any other, so the picture becomes a
  searchable PDF by the same code a scanned PDF does.

## The entry point

### `convert()`

Text from a picture or a PDF as `target_ext`: "txt", or "ocrpdf" (a
searchable PDF, written as `<stem>.pdf`). `lang` is the person's choice
(a key of LANGS). `max_scanned` is the number of pages the job was priced
for (the menu's count): a file with more scanned pages than that is
refused before any is read, so a job never recognises a page it was not
sold. None: no limit but OCR_MAX_PAGES. Raises ConversionError, and
OutOfMemory for a machine too small for the picture.

- At `animated = convert_utils.gif_is_animated(in_path) if src_ext in formats.ANIMATED_CAPABLE …`:
  The file settles which of the two a GIF, WEBP or APNG is, as it does in the dispatcher.

## The build-time check

### `selftest()`

Exits 1 with a one-line reason unless the language data is there and the
engine reads a line of English and a line of Russian back. Run when the
image is built (convert_bot/Dockerfile), so a build that cannot read text
never ships. Under five seconds.

- `font = pymupdf.Font("helv")` — has the Cyrillic letters; no font file is needed
