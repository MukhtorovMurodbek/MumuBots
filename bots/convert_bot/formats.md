# `formats.py`

[↑ convert_bot](README.md) · [code](formats.py)

What this bot converts, what it converts it to, and what this particular
host can actually do.

Three things live here and nothing else -- no conversion code, no Telegram,
no database. `convert_utils.py` and the two `*_convert.py` modules do the
work; `bot.py` builds the buttons; this file is the single place that answers
"is .heic a thing the bot takes, and if so what can it become".

\## Why the answer is not a constant

Every backend past Pillow is optional. `pymupdf` is what reads a PDF or an
EPUB, `pillow-heif` is what reads an iPhone photo, `openpyxl` is what reads a
spreadsheet, and ffmpeg is a system binary that may be built without the
encoder a particular target needs. Any of them can be missing -- on a
developer's laptop, on a host whose build failed halfway, on a slimmer image
somebody deploys later.

A bot that advertised a format it could not produce would take somebody's
Stars and hand back an exception, so the matrix below is \*\*declared in full
and then filtered by what imports\*\*. `probe()` runs once at startup and the
menu, `/formats`, and the invoice all read the filtered view. Nothing that
cannot run is ever offered, and nothing has to be edited to say so.

\## The shape of the matrix

A file has an \*\*extension\*\*, which normalises to a \*\*format\*\* (`.jpeg` and
`.jfif` are both `jpg`), which belongs to a \*\*category\*\* (image, video,
audio, document, data, subtitle). Targets are looked up per format first and
per category second, because within one category the answers genuinely
differ: an EPUB converts to a PDF and a PDF does not convert to an EPUB.

## Categories

Seven, and they are user-visible: /formats has a button per category and the
conversion menu groups by them. Vector graphics (svg) live in `image`
deliberately -- svg answers the same question a png does ("a picture"), and
splitting it out would give /formats a category with one member in it.

## Extensions in, formats out

ALIASES collapses the spellings of one format onto one name. It runs before
anything else looks at an extension, so the rest of this file -- and every
TARGETS list, every button, every price line -- deals in one spelling per
format. `.jpeg` and `.jpg` must not be offered as two separate targets.

_At `"pps": "ppt", "ppsx": "pptx", "pptm": "pptx",`:_

A slide show is the same file as its deck; PowerPoint only opens it differently.

_At `COMPOUND_EXTENSIONS = (("tar.gz", "tgz"), ("tar.bz2", "tbz2"), ("tar.xz", "txz"))`:_

An archive's name ends in two extensions, and only the pair says what it is:
`photos.tar.gz` is not "a .gz". ConvertBot names them by the one-word form
(tgz, tbz2, txz) because every format name here is a single plain word that
ends up in a file name and a callback -- see tests/test_untrusted_names.py.

### `compound_extension()`

`photos.TAR.GZ` -> `tgz`; None when the name does not end in one.

_At `RAW_FORMATS = ("dng", "cr2", "cr3", "nef", "arw", "raf", "orf", "rw2")`:_

Camera RAW, which is a picture to the person who sent it and arrives with an
extension of the maker's choosing. All of them are `image` sources and share
one list of targets (TARGETS_BY_FORMAT): see raw_convert.py.

_At `SOURCES = {`:_

What each category takes IN. Not the same as what it puts out: this bot
reads a .psd and a .mobi and writes neither, which is the normal shape of
a converter -- reading a format is somebody else's library problem and
writing one is a design decision.

_At `ARCHIVE: ("zip", "tar", "tgz", "tbz2", "txz", "7z", "rar"),`:_

tgz, tbz2 and txz are .tar.gz, .tar.bz2 and .tar.xz -- see
COMPOUND_EXTENSIONS. RAR is read and never written: the format's own
tools are not free software and nobody needs a RAR made for them.

_At `CATEGORY_OF: dict[str, str] = {}`:_

The reverse index, built once.

_At `ANIMATED_CAPABLE = ("gif", "webp", "apng")`:_

GIF, WEBP and APNG are each two different files wearing one extension: a
single frame, which is a picture, and an animation, which is a video with no
sound. Guessing from the extension is wrong in both directions and both
wrong answers are bad ones -- offering PNG for a five-second animation
throws away every frame but the first, silently, and refusing PDF for a
one-frame GIF withholds a conversion that would have worked.

So the file is asked instead. `convert_utils.gif_is_animated()` reads the
header (cheap: Pillow does not decode the frames) and the answer is passed
to `category_of()` and `targets_for()` as `animated=`. GIF_IS_VIDEO is only
the fallback for when nobody looked, and it points at the safe direction:
keeping frames that turn out not to exist costs nothing, losing frames that
do costs the whole file.

_At `ANIMATED_TARGETS = ("mp4", "webm", "gif", "webp", "apng", "mkv", "mov", "png", "jpg")`:_

What an animation converts to, whichever of the three it arrived as. PNG and
JPG are on it deliberately -- "just the picture" is a real request,
and ffmpeg answers it with the first frame.

## What converts to what

Order is the order the buttons appear in, and it is by demand rather than
alphabetical: whatever most people came for is first, and POPULAR_COUNT
below cuts each list where the rest stops being worth a first screen.

_At `IMAGE: (`:_

The last two read the text in the picture (ocr_convert.py): they are
past POPULAR_COUNT, so they sit in "More formats", and they are only
offered where the host has the language data (the "ocr" requirement).

_At `ARCHIVE: ("zip", "7z", "tgz", "tar", "txz", "tbz2"),`:_

ZIP first: it is what a phone can open, which is most of why anybody
sends an archive here. RAR is a source only, so it is not in this list.

_At `PRESENTATION: ("pdf", "pptx", "ppt", "odp", "png", "jpg", "txt"),`:_

Slides (LibreOffice Impress): the decks to each other, PDF to hand in, a picture per slide,
or the words alone.

_At `}`:_

DOCUMENT has no category-wide answer worth having: see below.

_At `TARGETS_BY_FORMAT = {`:_

The per-format overrides, which for documents \*are\* the rule. A PDF and an
EPUB are both documents and share almost no targets: one is a finished
page, the other is reflowable text, and the arrow between them only points
one way.

_At `"pdf": ("docx", "pptx", "png", "jpg", "txt", "ocrpdf", "html", "webp", "svg", "tiff", "o …`:_

pptx/odp: editable slides through LibreOffice's PDF import (FORMAT_NEEDS / TARGET_NEEDS).

_At `"docx": ("pdf", "odt", "doc", "html", "md", "txt", "rtf", "png", "jpg"),`:_

Word and OpenDocument text through LibreOffice (2.1.7); docx to md/html keeps mammoth.

_At `"svg": ("png", "pdf", "jpg", "webp"),`:_

An SVG is a picture, so it sits in the image category, but it is drawn
by the PDF engine rather than by Pillow, and it has no business being
offered "convert to ICO at whatever size is guessed".

_At `**{raw: ("jpg", "png", "webp", "avif", "heic", "tiff") for raw in RAW_FORMATS},`:_

A raw photo is developed into a picture; there is no PDF of one, and no
ICO of a 24-megapixel negative. TIFF is 16 bits per channel, for editing.

_At `"gif": ("mp4", "webm", "webp", "apng", "png", "jpg"),`:_

An animated source keeps its frames. A still target here would quietly
discard all but the first, which is the one mistake that looks like it
worked.

_At `"flac": ("wav", "aiff", "mp3", "m4a", "ogg", "opus", "aac"),`:_

Lossless in, lossless out first: somebody sending a FLAC is usually not
looking for a 128 kbps MP3.

_At `POPULAR_COUNT = 6`:_

What each format shows before "More formats". Six is what fits two rows of
three on a phone without the message itself scrolling, and the seventh
onwards is where the tail of every list starts.

## What each format needs to exist at all

The keys are requirement names; probe() fills in which ones are met.

```text
heif      pillow-heif, for HEIC/HEIF -- which is what an iPhone photo is.
avif      AVIF, native in Pillow 11+ and via pillow-heif before that.
jp2       JPEG 2000, which needs Pillow built against OpenJPEG.
mupdf     PyMuPDF: PDF, EPUB, MOBI, FB2, CBZ, XPS, SVG, and HTML or text
          rendered onto a page.
mammoth   .docx to semantic HTML, which mupdf then renders. Reading a
          .docx, not writing one.
pydocx    python-docx, which is what *writes* a .docx. A different
          library from mammoth and a different direction -- which is why
          DOCX appears in TARGET_NEEDS as well as in FORMAT_NEEDS.
markdown  Markdown to HTML, likewise.
openpyxl  .xlsx.
yaml      PyYAML.
xml       defusedxml -- the *safe* XML parser, and the only one this bot
          will point at a stranger's file. See data_convert.py.
ffmpeg    the binary, on PATH, with the encoder the target needs.
rawpy     LibRaw, which develops a camera raw photo (and brings numpy).
bsdtar    libarchive's tar: reads 7z and RAR, writes 7z. ZIP and TAR are
          Python's own and need nothing.
ocr       text recognition (ocr_convert.py): PyMuPDF, whose MuPDF links the
          Tesseract engine (`Pixmap.pdfocr_tobytes`), and the language data
          in TESSDATA_PREFIX. There is no tesseract binary to look for.
          Without it no OCR target is offered, and a PDF of scanned pages
          is refused as before.
```

Pillow itself is a hard dependency: without it the bot does not import, so
it is not listed against anything.

A format needs the same thing whichever side of the arrow it is on, with
one exception -- TARGET_NEEDS below.

_At `"ocrpdf": ("ocr",),`:_

A pseudo-target (the searchable PDF; its file is `<name>.pdf`): never a
source, so this is only ever read for the target side.

_At `TARGET_NEEDS = {`:_

The one place a format's requirement depends on which side it is on. Every
still image Pillow can open, it can also write straight into a PDF page --
no PDF engine involved, because there is no PDF being \*read\*. Without this
override, a host with no PyMuPDF would refuse the single most asked-for
conversion in the bot for no reason at all.

_At `(DOCUMENT, "docx"): ("pydocx", "mupdf"),`:_

Reading a .docx is mammoth's job; writing one is python-docx's, and the
text to put in it comes from the PDF engine. A host with mammoth and no
python-docx reads .docx files and does not offer DOCX as a target, which
is exactly what this table is for.

_At `(IMAGE, "txt"): ("ocr",),`:_

The words in a picture come from the text recognition; the words in a
PDF (FORMAT_NEEDS has no "txt" entry) come from the PDF engine alone,
which is why a scanned PDF still converts to TXT where there is no OCR.

_At `(DOCUMENT, "pptx"): ("office", "pdfimport"),`:_

A PDF opened as slides needs LibreOffice's PDF import as well.

_At `ENCODERS = {`:_

Formats that are only reachable through ffmpeg, and the encoder each one
needs. nixpkgs' ffmpeg carries all of these, but a leaner build somewhere
else may not, and "Unknown encoder 'libopus'" is not a message any user
should have to read. Probed once, at startup, from `ffmpeg -encoders`.

_At `FFMPEG_CATEGORIES = (VIDEO, AUDIO, SUBTITLE)`:_

Which categories go through ffmpeg at all. A format named in ENCODERS is
still Pillow's job when the source is a still image -- webp and gif are in
both worlds.

## What this host turned out to have

### `bsdtar_path()`

libarchive's tar, which reads 7z and RAR and writes 7z: `bsdtar` where it
is installed as that (Debian's libarchive-tools), and on Windows the
system's own tar.exe, which is the same program. CBOT_BSDTAR names one
explicitly. GNU tar is not it, and is never mistaken for it.

### `_ffmpeg_encoders()`

The encoder names this ffmpeg was built with.

One subprocess, once, at startup. If it fails -- no ffmpeg, a build with
no `-encoders`, a host that took too long -- the answer is the empty set,
which turns every ffmpeg target off rather than promising one.

- At `if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] in "VAS":`:
  Every entry is "FLAGS name description"; the header lines above the
  separator have no six-character flag field to match.

### `_ocr_available()`

Whether text can be recognised here: PyMuPDF with Tesseract in it, and the
language data on disk (ocr_convert.available). Imported here, not above:
ocr_convert imports convert_utils, which imports this module.

### `probe()`

Work out what this host can do. Idempotent; called once from main().

### `_encoder_ok()`

True unless ffmpeg is the one who would have to write this and cannot.

- `return True` — written by Python, not by ffmpeg (e.g. txt)

## The questions the rest of the bot asks

### `normalise()`

`.JPEG` -> `jpg`. The only way an extension should ever enter.

### `category_of()`

Which category a source file belongs to, or None if it is not one the bot reads.

`animated` is the answer for the three extensions that can be either; pass
it whenever the file is on disk to be looked at. Without it a GIF is
answered as video and a WEBP as an image, which are the defaults that lose
the least when they are wrong.

### `declared_targets()`

Everything this format converts to on a host that has everything.

The unfiltered view: what the tests check, and what nothing user-facing
should use.

### `targets_for()`

Everything this format converts to \*here\*. What the buttons are built
from, and the list a chosen format is checked against before anything
runs.

### `popular_targets()`

The first screenful. A short list stays whole -- there is no point in
hiding two buttons behind a button.

### `supported_sources()`

Every extension of this category this host can read, in the order it
is declared -- which is roughly by how common it is.

### `supported_targets()`

Every format this host can write, for sources of this category.

In the category's own declared order first, so that /formats reads by
demand like the buttons do, and anything a single format adds on its own
(an EPUB's PDF, a GIF's MP4) after it.

### `counts()`

(formats in, formats out, source-to-target pairs) -- for /formats,
which should not contain a number somebody has to remember to update.

### `find_format()`

`png`, `.PNG`, `png file`, `image/png` -> `png`, when it is known.

Used by `/formats <thing>` and by the catch-all handler, so that somebody
who just types "heic" at the bot gets an answer rather than a form letter.

## What a conversion is allowed to cost in work rather than in bytes

The price is calculated from the file size, and size is a poor proxy for
work here in a way it was not when this bot only did images and ffmpeg: a
900 KB PDF can be four hundred pages, and rendering all of them is minutes
of CPU sold for the free tier. These are the ceilings that stop the gap
between "small file" and "small job" from becoming everybody else's
problem, and they are the reason the pricing model did not have to change.

_At `MAX_PAGE_MEGAPIXELS = int(os.environ.get("CONVERT_MAX_PAGE_MEGAPIXELS") or 100)`:_

The largest a single page may be once it is drawn as a picture, in
megapixels. A page's size is written into the file, so a 500-byte PDF can
ask for one nine thousand points on a side -- 350 megapixels, and a gigabyte
to draw, which MuPDF does without complaint and a 1 GB container does not
survive (measured 2026-09-29; MuPDF refuses from about 9,500 points). 100 is
66 by 66 inches at the 150 dpi pages are drawn at; an A0 poster is 35.

_At `MAX_BATCH = int(os.environ.get("CONVERT_MAX_BATCH") or 20)`:_

How many files one batch may hold. Telegram caps an album at 10; a longer
run of separate messages is collected the same way, and this is where it
stops.
