# `merge.py`

[↑ downloader_bot](README.md) · [code](merge.py)

An album as one file: a PDF of its photos, or a ZIP of everything in it.

The two buttons under a delivered album (bot.py, merge_callback) end up here,
with the files already on disk. Nothing is kept: whoever calls this deletes
every input and the output as soon as the output has been sent.

THE PDF IS WRITTEN HERE, BY HAND, because the one thing a PDF of photographs
needs -- a page the size of the picture, with the picture on it -- is a few
dozen lines, and a library for it (PyMuPDF) is a binary dependency this bot's
image does not otherwise carry. What it writes:

```text
* one page per image, as many points as the image has pixels, so a 1080 x 1350
  post is a 1080 x 1350 pt page; scaled down, proportions kept, when a side
  would pass 14400 (the largest page Acrobat opens);
* a baseline or progressive JPEG in RGB or grayscale goes in exactly as it
  came -- the same bytes, no second generation of compression -- as a
  DCTDecode stream. Everything else (PNG, WEBP, GIF, CMYK, a JPEG with an
  EXIF turn, anything with an alpha channel or a palette) goes through
  Pillow: turned upright, flattened onto white, converted to RGB and written
  as a JPEG at quality 92;
* streamed to the output file as it goes -- each image is read, written and
  let go before the next is opened, so the memory a PDF takes is one decoded
  image, not the album -- with the cross-reference table built from the
  offsets the file actually has;
* an image over 20 megapixels is refused (PictureTooLarge). The size is read
  from the file's header, so the refusal costs nothing; decoding is what
  would not be affordable on a small machine;
* only a still JPEG, PNG, WEBP or GIF is a photo here. Pillow's open() takes
  some forty formats whatever the file is called, and a few of them decode
  inside open() (an ICO's embedded PNG), which would get past the check
  above; an animated picture decodes every frame's canvas. Neither is a
  photo (NotAPhoto), found before a pixel is decoded -- a WEBP's animation
  flag and canvas, and a GIF's canvas, from the file's first 30 bytes, before
  Pillow opens it. build_pdf leaves such a file out, as the bot leaves a video
  out of an album's PDF, and builds from the photographs; only when fewer
  than two are left is there no PDF (NothingToMerge). A picture that is too
  big is not skipped but refuses the whole PDF: it is one the person expects
  to see;
* a hostile file's structure is judged before Pillow reads it: _check_header
  walks the JPEG's segments (to the first SOS), the PNG's chunks and the
  GIF's blocks (to the end), the WEBP's chunks, in constant memory and linear
  time, and refuses a file with more parts than a photo has (see
  MAX_HEADER_PARTS); a file cut short inside its structure is PhotoCutShort,
  not left out; a side over MAX_SIDE is PictureTooLarge;
* one PDF is built at a time in the whole process (build_pdf_queued): a
  decode is a few hundred MB, and the five bots share one container. The gate
  stays shut until the build's thread has really ended, even if the caller was
  cancelled (a thread cannot be).
```

The ZIP stores its files, without compressing them: they are photographs and
video, which are compressed already, so deflate would spend the time and save
nothing. Its entries are named 01.jpg, 02.mp4 ... -- the position in the post
and the extension of the file that was downloaded, never a name that came from
the other end of the wire.

This module knows nothing about Telegram or the network.

_At `MIN_PDF_PHOTOS = 2`:_

What a PDF of this bot's will take, and what it will not.

- `MIN_PDF_PHOTOS = 2` — a PDF of one photograph is the photograph

- `MAX_MEGAPIXELS = 20` — the largest these platforms serve is X's 4096 x 4096 (16.8 MP). Decoding one

_At `MAX_PAGE_POINTS = 14400`:_

page peaks (measured) at 321 MB for a 20 MP WebP, 269 MB for a 16.8 MP WebP and
240 MB for a 20 MP LA PNG, in a process the five bots share: see pdf_gate

- `MAX_SIDE = 65_500` — JPEG's own ceiling is 65,500 a side: a 20,000,000 x 1 PNG (20 MP) passes the megapixel

_At `JPEG_QUALITY = 92`:_

check and then cannot be written as a JPEG, so a longer side is refused as too large

_At `MAX_HEADER_PARTS = 1000`:_

Pillow reads a file's whole header inside Image.open(), before any check here, and
keeps what it reads: a JPEG's every segment (applist), a GIF's comment (by `+=`, which
is quadratic), a PNG's every chunk. Measured on a valid 64 x 48 picture: 6 million
4-byte APP5 segments (41 MiB) cost 1.32 GB and 8.9 s; COM segments (19 MiB) 392 MB; tiny
unknown PNG chunks (19 MiB) 217 MB and 4 s; a GIF of 1-byte comment sub-blocks took 1.6 s
at 0.8 MB and did not finish at 3.2 MB (and it runs inside the PDF gate, so every PDF
waited). \_check_header therefore walks the header itself, in constant memory, and refuses
a file with more than this many segments / chunks / blocks / sub-blocks before the first
picture data. A real photo has tens, a big ICC profile a hundred or so. A JPEG is walked to
its first SOS (libjpeg does the rest in C); a PNG and a GIF to their end, because Pillow
also parses what follows the picture (load_end(), the comment loop at any frame, the
frame count) in Python: a PNG's chunks other than IDAT are capped at this too, its IDATs at
MAX_PNG_IDATS (a real 48 MB PNG in 8 KB IDATs has about 6,000), a GIF's comment
sub-blocks at this and ALL its sub-blocks at MAX_GIF_SUB_BLOCKS (a real 48 MB GIF in
255-byte sub-blocks has about 190,000). A second frame in a GIF is an animation.

_At `_MAX_JPEG_FILL = 4096`:_

Bytes of fill or junk between a JPEG's segments that are tolerated (Pillow skips them one
at a time, in Python: a 48 MB run of 0xFF would take it about a minute).

_At `MAX_JPEG_SCANS = 200`:_

A progressive JPEG has about ten scans; libjpeg has no limit, and every scan of a
20-megapixel picture costs it a pass over the coefficients (measured 0.26 ms for
a 17-byte scan: 666 KB of scans held the PDF gate for 7.9 s, 48 MB would be ~13
minutes, in a thread that cannot be stopped). Counted over the whole file, in C.

- `_CHUNK_HEAD = struct.Struct(">I4s")` — a PNG chunk's length and type

_At `PHOTO_FORMATS = ("JPEG", "PNG", "WEBP", "GIF")`:_

The only formats a photo may be in. Not a list of what Pillow can read: open()
tries ~40 plugins whatever the extension says, and one of them (ICO) decodes
a PNG of any size inside open(), before the megapixel check can run.

### `MergeError`

Something this module will not or cannot build.

### `PictureTooLarge`

An image over MAX_MEGAPIXELS. Refused from its header, before a pixel
is decoded.

### `NotAPhoto`

A file that is not a still picture in one of PHOTO_FORMATS: another
format, or an animation. Found before a pixel is decoded. build_pdf leaves
it out of the PDF rather than raising it.

### `PhotoCutShort`

A JPEG, PNG or GIF whose file ends inside its own header: a photo that
was cut. Not NotAPhoto -- build_pdf does not leave it out, so the person is
told something went wrong with it (as for one cut short in its picture
data) instead of getting a PDF with a page quietly missing.

### `NothingToMerge`

No file was given, or not enough of them were photographs to make a PDF
of (build_pdf left the others out).

## Names

### `output_name()`

`instagram_20261007-1432.pdf`: what the person sees the file called.

UTC, like every other file this family names after the time. The platform
is cut down to letters and digits, though it comes from a list this bot
keeps, because it is going into a filename that someone else's software
will open.

### `_safe_ext()`

The extension of a file on OUR disk, or `default` if it is not plainly
one. resolvers.download() has already made that so; this is the second
lock on the same door, because the name goes into an archive that is
sent to somebody.

### `entry_name()`

`01.jpg` for the first file, `02.mp4` for the second. From the position
and from the downloaded file's own extension; nothing the source said the
file was called.

## ZIP

### `build_zip()`

Every file in `paths`, in order, stored (not compressed) in one ZIP.
Returns how many went in.

- At `info = zipfile.ZipInfo(entry_name(number, path, width), date_time=stamp)`:
  A ZipInfo made here rather than archive.write(path, name): that
  would copy the file's mode and modification time into the
  archive, which are the server's and not worth sending.

## PDF

_At `_NEXT_MARKER = re.compile(rb"\xff[^\x00\xd0-\xd7\xff]")`:_

The first real marker after a scan's entropy-coded data: a 0xFF followed by
anything but 0x00 (a stuffed 0xFF), 0xD0-0xD7 (a restart) or another 0xFF
(fill: the marker is the last of the run). Found by the regex engine, in C --
a walk in Python spends microseconds per byte, and a hostile file is all 0xFF.

_At `_MAX_SEGMENTS = 10_000`:_

A real JPEG has tens of segments between its scans, a hundred with a big
ICC profile. A file with this many is not one, and walking 12 million
four-byte segments in Python would take seconds: Pillow decodes it instead,
under the megapixel limit.

### `_jpeg_layout()`

(width, height, components, length) of a JPEG that can go into a PDF
untouched, or None.

"Untouched" means what DCTDecode takes: baseline (SOF0) or progressive
(SOF2), 8 bits, one or three components, with a size in the header, and a
clean end. The walk below follows the file marker by marker, through the
entropy-coded data of every scan, to the EOI -- so `length` stops at the
end of the first picture, and whatever follows it (the second image of a
phone's MPO, a trailing blob) is not carried into the PDF. A file that
runs out before an EOI is not embedded; it goes to Pillow, which says so.

- `if marker == 0xFF:` — fill bytes before a marker: skip the whole run
- `pos = found.start() - 1` — on the last 0xFF, with the marker after it
- `if marker == 0xD9:` — EOI
- `if marker == 0x01 or 0xD0 <= marker <= 0xD8:` — no length
- `return None` — extended, lossless, arithmetic: not DCTDecode's
- `if marker == 0xDA:` — SOS: entropy-coded data, up to the next real marker

### `_upright()`

The picture the way up the camera meant it. Only when there is
something to do: exif_transpose copies, and a 20-megapixel copy is
60 MB. The original is let go as soon as the turned one exists.

- `from PIL import ImageOps` — here, so a stand-in for Pillow (tests/stubs) still imports

### `_flatten()`

RGB, with any transparency laid over white. A JPEG has no alpha, and
dropping it instead of flattening turns every transparent pixel into
whatever colour happened to be stored under it -- usually black.

- At `image = image.point(lambda value: value * (1 / 256)).convert("L")`:
  16 bits a pixel: a plain convert() clips at 255 and returns a white page.

### `_count_scans()`

Refuses a JPEG with more than MAX_JPEG_SCANS start-of-scan markers. Inside a
scan's data a 0xFF is always stuffed (0xFF 0x00) or a restart, so FF DA is a
scan, or - in a header segment's own bytes - a coincidence the limit is far
above (a megabyte of ICC profile has ~16). Read in 1 MiB pieces; the byte
before each piece is carried so a marker split across two is still seen.

### `_walk_jpeg()`

Segment by segment, jumping over each by its length, from the SOI to the
first SOS (or EOI). Linear in the number of segments, and that is capped.

- `if pair[0] != 0xFF or pair[1] in (0x00, 0xFF):` — junk, a stuffed byte or fill
- `if marker in (0xDA, 0xD9):` — SOS, EOI: the picture starts, or ends
- `if marker == 0x01 or 0xD0 <= marker <= 0xD8:` — no length

### `_walk_png()`

Chunk by chunk (length, type, data, CRC), jumping over each, from the
signature to IEND (or the end of the file). The WHOLE file, not up to the
first IDAT: Pillow's load_end() walks the chunks after the picture in
Python too. Chunks other than IDAT are capped at MAX_HEADER_PARTS in all,
IDATs at MAX_PNG_IDATS. Read in 64 KiB windows, because 50,000 IDATs of 12
bytes are 50,000 calls otherwise.

- `if pos == size and idats:` — the file stops on a chunk boundary after the picture
- `if not offset:` — fewer than 8 bytes left: inside a chunk's header

### `_gif_sub_blocks()`

Past the sub-blocks that start at `pos` (each a length byte and that many
bytes, ended by a zero length): the position after the terminator. `count` is
[all sub-blocks so far, comment sub-blocks so far], kept across calls. Read
in 64 KiB windows and counted in a tight loop: a call per sub-block, 300,000
of them, is too slow.

### `_walk_gif()`

Block by block, the WHOLE file, to the trailer (or the end of the file):
extension blocks and the picture's own data, sub-block by sub-block, every
one counted. Pillow's comment loop (`+=`) runs for a comment anywhere in the
file, and its is_animated / n_frames skip a frame's data a sub-block at a
time, in Python. A second image descriptor is a second frame: an animated
picture, which is no photo. Pillow skips a stray byte at a time, in Python,
so anything that is not a block is refused.

- `pos = 13 + (3 * (2 << (head[10] & 7)) if head[10] & 0x80 else 0)` — past the global colour table
- `if not block or block == b"\x3b":` — trailer, or the file just stops
- `if block == b"\x21":` — an extension block
- `elif block == b"\x2c":` — an image descriptor, and its data
- `pos += 3 * (2 << (descriptor[8] & 7))` — past the local colour table
- `pos = _gif_sub_blocks(source, pos + 1, count, False)` — +1: the LZW code size byte

### `_walk_webp()`

Chunk by chunk (fourcc, little-endian size, data padded to even), up to
the first picture chunk.

### `_check_header()`

Refuse, from the file's start and before Pillow is asked to open it,
what open() itself would pay for. Some Pillow versions build the WEBP
animation decoder, with a canvas for every frame, inside open() -- a
96-byte file claiming 20 MP measured 403 MB there -- so whether the picture
is animated, or too big, is read here from the VP8X chunk (flags at byte 20,
canvas size as 24-bit little-endian values less one at 24 and 27). A GIF's
logical screen (bytes 6-9) is its canvas. Then the header's own parts are
counted (MAX_HEADER_PARTS): open() would otherwise read and keep millions
of them. Anything else, and any header that does not say, is left to the
checks after open().

### `_animated()`

More than one picture in it, now that Pillow has opened it. Every frame
of a WEBP or GIF costs a canvas the size of the whole picture. An MPO -- a
phone's JPEG with a second picture after the first -- counts its pictures
as frames too, but it is one photo: the first picture, which is the only
one this module reads.

- `return False` — \_walk_gif has seen the whole file and refused a second frame; is_animated would walk it again in Python

### `_open_photo()`

The file opened as a Pillow image, if it is a still photograph of a size
this module takes: everything that can be told without decoding a pixel.
PictureTooLarge if it is over the limit, NotAPhoto if it is not a still
JPEG, PNG, WEBP or GIF. The caller closes the image.

- At `image = Image.open(path, formats=PHOTO_FORMATS)`:
  Pillow warns past ~89 MP; the limit that matters is this module's, below. (Not
  caught with warnings.catch_warnings(): that swaps a process-wide list,
  and this runs in a pool thread beside every other bot's threads.)
- At `if "could not create decoder object" in str(exc):`:
  A corrupt or cut WEBP: libwebp refuses it in open(). Nothing of it can be
  shown, and the page tree is numbered up front, so it is left out like any
  other file that is not a photo. (A JPEG or PNG cut short raises later, in
  load(), and stays a failure: the person should know a photo was cut.)
- `width, height = image.size` — from the header: nothing is decoded

### `_prepare()`

(width, height, colour space, JPEG bytes) for one page of the PDF. The
JPEG bytes are a `bytes`, or a memoryview of the file's own when only the
start of the file is the picture: both go to out.write() and len().

- At `return (w, h, "/DeviceGray" if components == 1 else "/DeviceRGB",`:
  A view, not data[:length]: that copies the picture, and the file is already in memory.
- `_check_size(*image.size)` — the header is the file's word; this is what came out

### `_points()`

- At `text = f"{max(1.0, min(value, MAX_PAGE_POINTS)):.3f}".rstrip("0").rstrip(".")`:
  At least one point a side: a page of 0 pt is no page (the longest side is
  capped at MAX_PAGE_POINTS, so a very thin picture would round to it).

### `build_pdf()`

One page per photograph in `paths`, in order, written to `out_path`.
Returns the page count. A file that is not a still JPEG, PNG, WEBP or GIF
is left out, found before anything is written (the page tree is numbered
up front); NothingToMerge if none is left, or if some were left out and
fewer than MIN_PDF_PHOTOS remain. PictureTooLarge for an image over the
limit refuses the whole PDF, and so does whatever Pillow raises for a
photograph it cannot decode -- then `out_path` is partial and the caller
deletes it.

- At `total = 3 + 3 * pages`:
  Fixed numbering, so the page tree can be written before the pages are
  known to have worked: 1 catalog, 2 page tree, then three objects (page,
  its content, its image) for each image.
- `out.flush()` — on disk before the next image is opened

_At `_pdf_gate: "asyncio.Semaphore | None" = None`:_

One PDF at a time in the whole process (on a small container; see pdfs_at_once). Decoding a page peaks at 240-320 MB
(see MAX_MEGAPIXELS) and up to three merges can be in pool threads at once:
three 20 MP WebPs together measured 957 MB, which in a 1 GB container shared
by all five bots is the kernel killing every one of them. A ZIP only copies
bytes and needs no gate. Made on first use, like bot.\_slots(): a Semaphore
belongs to the event loop it first has to wait in, and the bot has one.

### `pdfs_at_once()`

How many album PDFs are built at once: DBOT_PDF_AT_ONCE when it is set, else one for
each 1024 MB of the bot's share of the memory (a decode peaks at 240-320 MB, and three
together measured 957 MB), from one up to four; one when the memory is not known.

### `build_pdf_queued()`

build_pdf in a pool thread, after any other PDF being built has
finished. Someone who waits sees what they would see anyway: the working
message.

- At `task = asyncio.ensure_future(asyncio.to_thread(build_pdf, paths, out_path))`:
  The thread as a task of its own, shielded from the caller. A thread
  cannot be cancelled: if the caller is (a restart, a timeout), the build
  runs on, and the gate must stay shut until it has really finished, or the
  next decode starts on top of it. Whoever ends it -- here, or the callback
  once the thread is done -- is the one that lets go of the gate.

#### `build_pdf_queued.finished()`

- `done.exception()` — seen, so an abandoned failure is not logged as never retrieved

#### `build_pdf_queued.discard()`

Nobody will be told about this file, and the thread may yet create it:
it goes when the thread is done.
