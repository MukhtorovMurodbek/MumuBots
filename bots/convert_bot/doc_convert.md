# `doc_convert.py`

[↑ convert_bot](README.md) · [code](doc_convert.py)

Documents: PDF, EPUB, MOBI, FB2, CBZ, XPS, DOCX, Markdown, HTML, plain
text -- and SVG, which is a document in every way that matters here.

Three libraries, all optional, all imported inside the functions that need
them so that a host without one still starts and simply does not offer those
formats (`formats.probe()` is what notices):

```text
PyMuPDF   opens all of the above except DOCX and Markdown, renders pages to
          pictures, extracts their text, and writes a PDF from HTML.
mammoth   turns a .docx into semantic HTML -- headings, lists, bold, tables
          -- which PyMuPDF then lays out.
markdown  the same trick for Markdown.
docx      python-docx, which writes the .docx that _to_docx below builds.
```

\## What "docx to PDF" means here, and what it does not

There is no LibreOffice in this image, so a .docx is not rendered the way
Word would render it: page breaks, columns, headers and footers, and exact
kerning are not preserved. What is preserved is the document -- its
headings, its emphasis, its lists and its tables -- reflowed onto A4. That
is the honest description and it is what the caption says, because a user who
expects a pixel-identical page and gets a reflowed one has been misled even
though the conversion "worked".

Adding LibreOffice would fix that and cost about a gigabyte of image and a
much slower cold start on every deploy, for one format pair. It is written up
in `CHANGES_v1.5.0.md` under \*Not done yet\* rather than done quietly.

_At `_MUPDF_NATIVE = ("pdf", "epub", "mobi", "fb2", "cbz", "xps", "svg", "html", "txt")`:_

Formats PyMuPDF opens directly, by extension. Everything else here has to
be turned into HTML or into a PDF first.

_At `WRITES = frozenset({"pdf", "png", "jpg", "webp", "tiff", "svg", "txt", "html", "md", "do …`:_

What convert() below can write. Declared rather than inferred so that
tests/test_conversions.py can check the whole matrix in formats.py against
the three backends that have to implement it -- a target advertised with no
branch to produce it is otherwise only found by a user picking it.

_At `_PAGE = "a4"`:_

The page a reflowed document is laid out on. A4 rather than US Letter: the
bot's users are not in the United States.

### `_open()`

The source as a PyMuPDF document, whatever it started as.

- At `pdf_bytes = _to_pdf_bytes(in_path, src_ext)`:
  docx and md have to become a PDF before there are pages to look at.

### `_check_page_sizes()`

Refuse a document with a page too big to draw, before drawing any.

The number of pages is capped and the size of each is not: it is a
rectangle written into the file, and drawing it costs its area in memory
however little else is on it. Checked for every page up front, so that a
poster on page 190 is not found after 189 pages of work.

_At `def _docx_to_html(in_path) -> str:`:_

---------- getting to HTML ----------

### `_source_html()`

- At `doc = _open(in_path, src_ext)`:
  A page of a PDF or an e-book already has a layout; MuPDF's own HTML is
  the closest thing to it that a browser can open.

_At `def html_to_pdf(source_html: str, out_path) -> None:`:_

---------- HTML to a laid-out PDF ----------

### `html_to_pdf()`

Lay HTML out onto pages and write a PDF.

`Story` is MuPDF's own flow layout: it is given a rectangle at a time and
says whether it has more to place, which is exactly the loop below. It
understands the subset of HTML and CSS that documents are actually made
of -- headings, paragraphs, emphasis, lists, tables, images -- and
ignores the rest rather than failing on it.

- At `writer = pymupdf.DocumentWriter(out_path if hasattr(out_path, "write") else str(out_path))`:
  DocumentWriter takes a path or a file object; \_to_pdf_bytes hands it
  a buffer, because a docx on its way to being rendered as pictures
  never needs to exist on disk as a PDF.

### `_to_pdf_bytes()`

A PDF of the source, in memory. Only used as a stepping stone -- when
the PDF is the answer, `_to_pdf` writes it straight to disk instead.

- `_fitz()` — a clear refusal now rather than an ImportError later

_At `def _render_pages(in_path, src_ext: str, target_ext: str, work_dir, stem: str) -> list[P …`:_

---------- pages as pictures ----------

### `_render_pages()`

One picture per page, at PAGE_DPI.

PNG comes straight out of MuPDF; everything else is that PNG handed to
Pillow, because there is no second encoder here worth having and the
intermediate is one file on disk rather than a second decode of the
document.

### `_page_svgs()`

A PDF page as SVG keeps its vectors, which is the whole reason to ask
for one rather than a PNG.

### `_pack()`

One page comes back as itself; many come back as a zip.

_At `def _to_text(in_path, src_ext: str) -> str:`:_

---------- text ----------

### `_to_text()`

- At `doc = _open(in_path, src_ext)`:
  The Markdown \*source\* is already text, so the useful answer is the
  text it renders to -- headings without their hashes, links without
  their brackets.

_At `_HEADING_RATIO = 1.25`:_

---------- the dispatcher ----------

## ...and back out to Word

Asked for: PDF to Word, at least the bare minimum with text and some images.

It is possible, and "the bare minimum" is the honest ceiling rather than a first
step towards something better. A PDF is a description of marks on a page:
glyphs at coordinates, with no paragraphs, no headings, no lists and no
tables in it. Anything that hands over a Word document with those things in
has \*inferred\* them, and every library that does it well -- pdf2docx and
the commercial engines -- carries a computer-vision stack to do the
inferring. pdf2docx alone pulls in OpenCV and NumPy, which is most of a
hundred megabytes of image for one format pair.

So this does the part that is not guesswork: PyMuPDF already groups glyphs
into lines and blocks and reports each span's font, size and weight, and
that is enough to write real paragraphs, bold where the PDF says bold, and
a heading where a line is markedly larger than the page's body text. The
pictures come out as pictures, in the order they appear. What does not
survive is the page layout itself: columns become sequential paragraphs,
tables become their text, headers and footers become paragraphs like any
other, and the page breaks are the PDF's rather than Word's.

The caption says all of that, because a Word document that looks nothing
like the PDF is a bad surprise and a Word document described as a reflow is
exactly what was asked for.

_At `_HEADING_RATIO = 1.25`:_

A line whose text is this much larger than the document's usual size is
treated as a heading. 1.25 is deliberately shy: over-calling headings in a
long document is worse than under-calling them, because Word's navigation
pane then fills with sentences.

_At `_MIN_IMAGE_PIXELS = 64 * 64`:_

Images smaller than this are page furniture -- rules, bullets, logos in a
footer -- and putting them in costs more than it gives.

_At `_MAX_IMAGE_PIXELS = 50_000_000`:_

And past this an image is left out rather than decoded: the size is what the
PDF says it is, a few kilobytes of it can declare a picture too large to hold
once decoded, and a Word document is not where a 200-megapixel photograph is
wanted. Skipped, like every other picture the writer will not take.

### `_body_size()`

The size most of this document's text is set in, which is what makes
'larger than the body' a question with an answer. The mode rather than
the mean: an average is dragged up by one big title on a short document,
and then nothing is a heading.

### `_to_docx()`

Everything the PDF actually says, as a Word document. See above for
what that does and does not include.

- At `bold = any(int(span.get("flags") or 0) & (1 << 4) for span in spans)`:
  Bit 4 of PyMuPDF's span flags is the bold bit.

### `_add_page_images()`

The pictures on one page, in the order the page lists them.

Extracted rather than re-rendered, so a photograph comes out as the
photograph rather than as a screenshot of it. Anything the writer refuses
-- an exotic colour space, a mask, a CMYK JPEG -- is skipped rather than
failing the conversion: a Word document missing one decoration is worth
having and an exception instead of a document is not.

- At `picture = PILImage.open(io.BytesIO(raw))`:
  Through Pillow, because python-docx only takes the handful of
  formats Word understands and a PDF can hold any of a dozen.
