# `convert_utils.py`

[↑ convert_bot](README.md) · [code](convert_utils.py)

The conversions themselves, and the Telegram Stars pricing table.

Three backends, chosen by what the source file is:

```text
Pillow          still images, in-process. Also the one that writes a PDF
                from pictures, which is the thing most people come here for.
ffmpeg          video, audio and subtitles, shelled out under a timeout.
doc_convert /   documents and structured data. Both import their heavy
data_convert    libraries lazily, so a host without them still runs.
```

`formats.py` decides \*what\* is offered; this file does it. The split matters:
the menu, the invoice and /formats all have to agree about what is possible
before any money changes hands, and they agree by reading one table rather
than by each knowing a little about ffmpeg.

Everything here is path-in, path-out and blocking, and every caller runs it
through `asyncio.to_thread`. Deliberately: the caller already has the upload
on disk and is going to stream the result straight back to Telegram, so
routing it through two `bytes` objects would mean holding the file in memory
twice over for nothing. With files past the Bot API's 20 MB that is a couple
of hundred megabytes per conversion.

_At `class Result(NamedTuple):`:_

---------- what the caller gets back ----------

### `Result`

One file to send, and enough about it to caption it honestly.

`items` is how many things went into it -- pages rendered, images bound
into a PDF, files converted -- and it is 1 for the ordinary case. `zipped`
says the file is an archive rather than the format on the button, which
happens whenever one input turns into more than one output and is the
single most surprising thing this bot does. The caption says so.

### `OutOfMemory`

The converter was killed for memory: the file is fine, the machine is
too small for it. On Railway's 1 GB container a 4K HDR video is this
(measured 2026-09-29) -- told as that, not as a file that could not be
read, which is what ffmpeg's last words made it look like.

### `_killed()`

Killed by SIGKILL -- the kernel's out-of-memory killer, when this
process did not do it itself (its own time limits raise before this is
asked).

### `remove_tree()`

Remove a folder and everything in it, whatever the modes inside say.

shutil.rmtree stops at the first thing it may not touch, and an archive can
hand over exactly that: a folder with no permissions at all (it cannot be
listed, so its files cannot be found), one that is read-only (its files
cannot be unlinked), a read-only file on Windows. The worker that unpacked
them owns all of it, so each of these is put right -- the entry
and the folder holding it made writable -- and the step that failed is
tried again. Never raises: this is cleanup, and what cannot be removed is
left for the sweeper (bot.\_sweep_uploads) to try again later.

A symlink is removed as a link and never followed or chmod-ed.

#### `remove_tree.retry()`

- At `target = str(target)`:
  rmtree's handler: (the call that failed, its argument, the error).
- `if target in tried:` — one more try each, or this loops on a file nobody can change
- `if target != path:` — never the folder the caller's tree sits in
- At `shutil.rmtree(target, **{_ON_ERROR: retry})`:
  The folder could not be entered, so nothing inside it was seen
  and the walk went on without it: do it over, now that it can be.

- At `waiting = [path]`:
  Every folder opened first, top down, with a list rather than
  recursion. The handler above can open a closed folder too, but it does
  so by starting another rmtree from inside rmtree, a few frames per
  level: a tree nested a few hundred closed folders deep -- one archive
  can be, when bsdtar was refused before the tree was opened up --
  would end in RecursionError, and the folder would stay (review,
  2026-10-08). On Windows a read-only file blocks its own removal, so
  files are opened there as well.

_At `_ON_ERROR = "onexc" if sys.version_info >= (3, 12) else "onerror"`:_

Python 3.12 renamed rmtree's handler argument (and gave it the exception
itself instead of exc_info); the image runs 3.11. The handler above takes
either, because it ignores its third argument.

_At `PROGRESS = None`:_

---------- how far a conversion has got ----------
Set by convert_worker.py, and only there: a callable taking (done, total) in
whatever unit the step counts -- seconds of media for ffmpeg, pages for a
document, files for an album. The bot turns it into the progress bar on the
conversion's status message. Nothing here depends on it being set, and a
failure inside it is never a failed conversion.

_At `_LOCAL_BOT_API = bool(os.environ.get("LOCAL_BOT_API_URL"))`:_

---------- limits ----------
The cloud Bot API refuses to let a bot download anything over 20 MB. Two
things lift that: MTProto for the big files alone (CBOT_API_ID and
CBOT_API_HASH -- see big_files.py), or a Bot API server of the operator's own
(LOCAL_BOT_API_URL, https://github.com/tdlib/telegram-bot-api). Either way
the transport carries 2000 MB, and since 2.0.0 that is the limit.

It was 100 MB, on the reasoning that the largest file is what a conversion
can finish in jobs.TIME_LIMIT_S. Size was a poor proxy for that: a minute of
a phone's 4K60 HDR video is about 500 MB and more work than half an hour
of a lecture recording the same size. On 2026-09-28 the upload limit was to be raised to a safe maximum. What makes a big file safe is the guards on
what it costs, not a number of megabytes:

```text
disk      checked against the size Telegram declares, before anything is
          downloaded (jobs.USER_STORAGE_MB, STORAGE_MB, MIN_FREE_MB);
transfer  stopped when it stalls rather than at a fixed time
          (big_files.STALL_S);
time      a conversion on course to miss its limit is stopped within its
          first minute and refunded, rather than run to the limit and
          then refunded (jobs.run_worker);
memory    capped by what is in the file -- MAX_MEGAPIXELS, MAX_PAGES,
          MAX_CELLS -- which a file's size never measured anyway;
money     priced by size, past 200 MB by the 100 MB, and
          tests/test_pricing.py holds every size up to this one to
          covering its worst case.
```

CONVERT_MAX_FILE_MB still lowers it. With neither route it is clamped back
to 20, so the bot cannot promise a limit Telegram will not let it meet.

- `big_files.configure("CBOT", "convertbot")` — shared/big_files.py serves DownloaderBot too

### `_big_files_refused()`

Telegram refused the API id and hash (big_files.verify): promise, and
take, only what the Bot API allows -- as if they had never been set.

### `max_file_mb_in()`

The largest upload taken in this chat. Over MTProto that is private
chats only; a local Bot API server has no such distinction.

_At `Image.MAX_IMAGE_PIXELS = 80_000_000`:_

Pillow's own guard against decompression bombs: a tiny file that declares
enormous dimensions and expands into gigabytes of pixels once decoded. The
default only \*warns\*, at 89 megapixels, and then decodes anyway -- which on
a container sized to the cheapest plan that fits is an OOM kill rather than
an error message. 80 megapixels is past any real camera and turns the whole
class of problem into a caught exception.

_At `MAX_FRAMES = int(os.environ.get("CONVERT_MAX_FRAMES") or 1200)`:_

How many frames of an animation are allowed through. A GIF is under the
size limit long before it is under the \*work\* limit -- a few hundred
kilobytes can be thousands of frames -- and this is the ceiling that keeps
an animated WEBP from becoming a minute of encoding sold for nothing.

_At `PAGE_DPI = int(os.environ.get("CONVERT_PAGE_DPI") or 150)`:_

What a page is rendered at when a document becomes pictures. 150 dpi is
readable on a phone and about a quarter of the pixels of 300.

_At `_MB = 1024 * 1024`:_

---------- pricing ----------
What a conversion is sold for is shared/pricing.py's rule -- its cost plus a
markup equal to the cost while it is small, growing more slowly after --
applied to the most this conversion is allowed to cost: how long it may run
(jobs.time_limit_for) and how much it may send back (jobs.big_send_limit).
Both limits come from the file and what it is estimated to become, so the
price does too, and the quote shown after the format is chosen is the
price charged.

Until 2.0.0 prices were a table of size tiers -- 20 ⚡ for 3 MB, 450 for
200, and ⚡200 more for every 100 MB after -- set for what was judged fair rather than for what a job costs. A 224 MB phone video to MP4 was
quoted ⚡2,100; under this rule it is ⚡36, and nothing is ever quoted below
what its job can cost (tests/test_pricing.py).

Size is still a rough proxy for work: a 900 KB PDF can be four hundred
pages. The answer is not a more elaborate price table, which nobody could
predict from looking at their file -- it is the ceilings in formats.py
(MAX_PAGES, MAX_ROWS, MAX_CELLS) and MAX_FRAMES above, which cap the work
instead of charging for it, and the time limit, which a job on course to
miss is stopped within its first minute of and refunded.

_At `FREE_UNDER_MB = 3`:_

Under this much -- the file and its estimated result together -- a
conversion is free, and held to jobs.FREE_TIME_LIMIT_S instead. In 1.6.0 it moved from 1 MB to 3.

### `price_for_size()`

None if a file this size cannot be taken at all (over MAX_FILE_MB);
otherwise what converting it into something its own size would cost.
The real quote waits for the format, which decides what comes out.

Credits, not Stars. Callers spend the result out of a balance; nothing
here charges anybody.

## What will come out, and how hard it will be to make

Until 1.7.0 a conversion was priced on the file that arrived, and the price
was quoted before anybody had said what they wanted it turned into. That is
the wrong number on both ends. A 78 KB .docx becomes a 1.6 MB PDF -- twenty
times the bytes to make, hold and send -- and a 12 MB video becomes either a
1 MB MP3 or a 70 MB GIF depending on which button is tapped. The request:

```text
"the price should be shown after calculating the approximate output size.
 the flow should go like this: User sends a file, chooses what to convert
 to, and they get their price which should be set including the input and
 output sizes. Maybe even consider the specific file types which would use
 more hardware than others."
```

So there are two tables here and neither of them pretends to be exact.

SIZE_RATIO is what the output tends to weigh against the input, looked up as
(source, target) first and (category, target) second, with a per-target
fallback. Compression ratios vary hugely with content -- a photograph and a
screenshot of the same pixel count are not the same PNG -- which is why the
quote says "about", and why the price is fixed at the quote: if the result
comes out bigger than estimated, the person who already agreed to a number
is not asked for more.

WORK_FACTOR is the part size cannot express. Encoding video is arithmetic
per frame per second of footage; copying a JPEG's pixels into a PNG is one
pass over a buffer. AVIF is worse still -- 187 CPU-seconds in 15 wall
seconds locally, and CPU is billed per core-second, so a multithreaded
encoder is not free just because it finishes quickly.

_At `BILLABLE = (os.environ.get("CONVERT_BILLABLE") or "sum").strip().lower()`:_

Bytes handled: the input plus the estimated output ("sum"), or the larger of
the two ("max"). Sum is the honest count of what a conversion moves through
the container -- down the wire, through the encoder, back up the wire -- and
is what the price is built on. "max" is here because it is the one other
defensible answer, and switching is one environment variable rather than an
edit.

_At `("docx", "pdf"): 20.0, ("docx", "html"): 1.2, ("docx", "md"): 0.3, ("docx", "txt"): 0.15,`:_

Documents, where the spread is widest. docx -> pdf is measured: a 78 KB assignment came back as a 1.6 MB PDF.

_At `("wav", "mp3"): 0.10, ("wav", "m4a"): 0.09, ("wav", "ogg"): 0.10,`:_

Audio: what comes out depends almost entirely on what went in.

_At `("png", "jpg"): 0.15, ("png", "webp"): 0.25, ("png", "avif"): 0.15,`:_

Images where the pair matters more than the target alone.

_At `("7z", "zip"): 1.6, ("rar", "zip"): 1.15, ("txz", "zip"): 1.25, ("tbz2", "zip"): 1.1,`:_

A solid 7z, an xz or a RAR opens into more than a ZIP's per-file deflate
packs it back into -- the phone-cannot-open-it case this exists for.

_At `("pptx", "pdf"): 0.8, ("pptx", "ppt"): 1.5, ("pptx", "odp"): 0.9, ("pptx", "txt"): 0.02,`:_

Office files through LibreOffice (2.1.7), measured on sample decks, documents and sheets.

_At `_RAW_RATIO = {"jpg": 0.25, "png": 3.0, "webp": 0.2, "avif": 0.15, "heic": 0.2, "tiff": 4 …`:_

A camera raw file is lossless-compressed sensor data, about a byte and a half
a pixel; a developed picture is something else per pixel, and the quote is
about "roughly", never "exactly". TIFF is 16 bits a channel, uncompressed.

_At `_RATIO_BY_CATEGORY = {`:_

Per category of source, then per target. Only where it differs from the
per-target default below.

_At `(formats.VIDEO, "mp3"): 0.10, (formats.VIDEO, "m4a"): 0.09, (formats.VIDEO, "aac"): 0.09,`:_

Pulling the sound out of a video, which is most of what is left.

_At `(formats.ARCHIVE, "zip"): 1.1, (formats.ARCHIVE, "tgz"): 1.1, (formats.ARCHIVE, "tbz2"): …`:_

Archives: what comes out depends on how well the SOURCE was packed, so
the pairs below carry the real differences and these are the middle.

_At `_RATIO_BY_TARGET = {`:_

The fallback: roughly what this format weighs relative to whatever it came
from. 1.0 for anything not worth an entry, which is most of them.

_At `_BYTES_PER_PAGE = {"png": 350_000, "jpg": 120_000, "webp": 90_000,`:_

Bytes a rendered page tends to weigh, so that a 900 KB PDF of four hundred
pages is not quoted as though it were four hundred kilobytes of PNG. Used
whenever the page count is known -- see document_pages().

_At `_WORK_BY_TARGET = {`:_

How much hardware a pair burns for its size. 1.0 is "one pass over the
bytes"; everything above it is work that size alone does not predict.

_At `(formats.DOCUMENT, "pdf"): 1.4, (formats.DOCUMENT, "png"): 1.4,`:_

Laying a document out on a page, or rendering one to pictures, is the
PDF engine doing real work rather than a re-encode.

_At `(formats.VIDEO, "png"): 1.0, (formats.VIDEO, "jpg"): 1.0,`:_

Video to a still or to sound is a decode and then almost nothing.

_At `(formats.ARCHIVE, "zip"): 1.4, (formats.ARCHIVE, "tgz"): 1.5, (formats.ARCHIVE, "tbz2"): …`:_

Opening an archive and packing it again: LZMA and bzip2 are slow in both
directions, and a solid 7z has to be read from its start.

_At `(formats.DOCUMENT, "odt"): 1.6, (formats.DOCUMENT, "doc"): 1.6, (formats.DOCUMENT, "rtf" …`:_

LibreOffice starts afresh for every conversion, which costs a few seconds whatever the size.

### `work_factor()`

How much hardware this pair costs for its size, as a multiplier.

- At `return RAW_WORK_FACTOR * _WORK_BY_TARGET.get(target_ext, 1.0)`:
  A raw decode is real work -- demosaicing, white balance, a curve --
  about twice a JPEG's per byte, before the target's own cost.

### `estimate_output_bytes()`

About how big the result will be. Never exact, and never presented as
exact -- see the note above.

`pages` overrides the ratio for the conversions where size is a bad proxy
for the work: a PDF is mostly its pictures, and rendering it to PNGs
produces a file per page whether the PDF was compressed well or badly.

### `billable_bytes()`

What the price is charged on. See BILLABLE.

### `ocr_job_pages()`

How many pages a conversion reads as text (ocr_convert.py): 0 when it
reads none, so it is not a text-recognition job at all.

A picture to TXT or a searchable PDF is one page. A PDF to either is the
pages the menu counted as scanned -- and a PDF with none, whose count could
not be had, or with more than ocr_convert.OCR_MAX_PAGES (one conversion
reads no more; its TXT is the text the other pages already have), is
today's PDF to TXT (doc_convert), priced and run as it always was. More
than one file (`files`) is an album, which is never read: each would be
converted for the price of one. ONE function, asked by the quote
(price_for_job), by the queue (bot.\_enqueue, which gives the job its time
limit) and by the dispatcher below, so the three cannot disagree on what
is being sold.

- `import ocr_convert` — here, not above: ocr_convert imports this module

_At `_OCR_TEXT_BYTES_PER_PAGE = 4096`:_

What recognised text weighs a page, and what the invisible text layer adds to a
searchable PDF's page (measured: about 5 KB, so twice that is allowed for).

### `ocr_limit()`

The time limit of a job that reads `read_pages` pages as text, from what
the quote knows: the file, its estimated result, the pair and the PDF's
page count. The one way the number is asked for -- by the price
(\_ocr_price) and by the queue (bot.\_enqueue) -- so the two cannot differ.
It is jobs.ocr_time_limit, which holds the formula.

The TXT of a PDF also has the text of the pages that have their own, which
the ordinary conversion of that PDF is given time for by its estimate (a
share of the file's size); the estimate of the pages read is by their
number alone, and so for a big PDF with one scanned page it would be the
smaller of the two. The time is made from the larger, so that reading a
page never gives a file less time than converting it without did.

### `_ocr_price()`

(credits, estimated output bytes) for a conversion that reads text.

Free only where an ordinary conversion is -- the file and its result under
FREE_UNDER_MB -- and it reads at most OCR_FREE_PAGES pages. Otherwise the
price of its worst case, as \_price makes it, with the time limit a job
that reads this many pages is given (ocr_limit).

### `price_for_job()`

(credits, estimated output bytes) for one conversion.

Free under FREE_UNDER_MB, however hard the pair is; above it, the price
of the most the job may cost -- its time limit, which grows with the size
and how hard the pair is to run, and its send limit, which grows with the
estimated result. A conversion that reads text is priced by the pages it
reads (ocr_job_pages, \_ocr_price); `files` is how many files it takes, and
an album is never one of those.

### `_price()`

- `import jobs` — here, not above: jobs imports this module

### `document_pages()`

How many pages a document has, read from its index rather than by
rendering it. None when the engine is missing or the file will not open --
the estimate falls back to a ratio, which is what it did before.

### `least_paid_price()`

What the cheapest paid conversion costs: the shortest time a paid job
is given and the least it may send back.

### `price_tiers_text()`

The price list: free under FREE_UNDER_MB, the least a paid conversion
costs, one worked example, and the most anything can -- every number
worked out by the same rule a quote is.

- `import i18n` — local import -- avoids a hard import-order dependency for callers that only need pricing math

_At `_OPENERS_REGISTERED = False`:_

---------- Pillow, and the openers that are not built in ----------

### `register_openers()`

Teach Pillow the formats that arrive as a plugin.

HEIC is the one that matters: it is what an iPhone camera writes, so it
is the format most likely to turn up from somebody who has never
converted a file before. Idempotent, and a missing pillow-heif is not an
error -- formats.py has already stopped offering HEIC in that case.

_At `_EXIF_ORIENTATION = 0x0112`:_

EXIF's Orientation tag, which says which way up the camera was held.

_At `_ORIENTED_BY_CONTAINER = {"HEIF", "AVIF"}`:_

HEIF and AVIF carry their orientation in the container (a rotation box the
decoder applies), and say that any EXIF orientation beside it is to be
ignored -- so it is not applied a second time here.

### `upright()`

The picture the way up it was taken.

A phone stores a portrait photograph as its sensor saw it -- on its side --
with a tag that says how to turn it. Pillow reads the tag and does not act
on it, and writing the picture to PNG, WEBP or a PDF page drops the tag, so
a photo sent as a file (Telegram turns one sent as a photo itself, which is
why nobody noticed) came out lying down. Turned here, once, on the way in.

Only when there is something to do: `exif_transpose` copies the picture, and
a 200-megapixel one is 600 MB. The original is closed as soon as the turned
one exists.

- `from PIL import ImageOps` — here, so a stand-in for Pillow (tests/stubs) still imports

### `gif_is_animated()`

Whether a GIF, WEBP or APNG actually has more than one frame.

Cheap -- Pillow reads the header, not the frames -- and it is what decides
whether "convert to PNG" is a reasonable offer or a way of throwing an
animation away.

_At `_IMAGE_TARGETS = {`:_

How each still target is written. Kept as one table rather than a chain of
ifs because the list is now long enough that a missing `elif` would be a
format silently falling through to "unsupported" instead of a syntax error.

```text
mode    what the image has to be converted to first. RGB drops alpha,
        which for a format with no alpha channel is the honest answer
        and for JPEG needs the white-background paste below.
fmt     Pillow's own name for it, where it differs from the extension.
kwargs  quality and the like.
```

_At `"ico":  ("RGBA", "ICO", {"sizes": [(256, 256), (128, 128), (64, 64), (32, 32), (16, 16)] …`:_

Both icon formats are a set of sizes rather than one picture, and both
have a ceiling: an ICO entry is at most 256 px, an ICNS 1024.

### `_flatten_onto_white()`

A transparent PNG saved as JPEG has to become \*something\* where it was
see-through, and black is the one answer nobody wants.

### `save_image()`

Write a picture that is already open as `target_ext`, by the table above,
and close it. The one place a still picture is written, so a raw photo
(raw_convert.py) comes out exactly as a JPEG of the same size would.

- `register_openers()` — the HEIF and AVIF writers are registered with the readers; a picture that was
- At `try:`:
  never opened from a file (a developed raw photo) has not done that
- At `img = img.convert("RGBA").resize((1024, 1024))`:
  ICNS refuses anything that is not one of its own square sizes,
  and refuses it after the encode has already started.

### `images_to_pdf()`

Every picture given, in order, one to a page.

The conversion this bot exists for as far as most people are concerned,
and the only one that takes more than one file. Returns the page count.

- At `if img.mode in ("RGBA", "LA", "P"):`:
  A PDF page is RGB. Anything with alpha would otherwise be
  rejected by the encoder after the whole set had been decoded.
- At `img.close()`:
  convert() and the flatten both return a new image, so the
  one that was opened is finished with either way.

_At `FFMPEG_TIMEOUT_S = int(os.environ.get("FFMPEG_TIMEOUT_SECONDS", "600"))`:_

---------- ffmpeg ----------
Two limits that matter on a small shared host:

```text
-threads   ffmpeg sizes its thread pool from the *host's* core count, not
           this container's slice, so an unbounded encode takes CPU away
           from every other user's messages for no throughput in return.
           And memory: each thread holds frames. Before an input's -i it
           caps only the decoder; the encoder and the filters size their
           own pools. Railway's container reports 48 cores and holds 1 GB,
           and there an ordinary 1080p video -- 19 MB -- asked for 945 MB
           before its first frame and was killed (2026-09-29). So the cap
           goes on all three: FFMPEG_CAP_IN before -i, FFMPEG_CAP_OUT
           after the encoder's arguments.
timeout    without one, a malformed input can leave ffmpeg spinning on a
           core until the container is restarted, while the user who sent
           it just sees nothing happen.
```

_At `_EVEN = "scale=trunc(iw/2)*2:trunc(ih/2)*2"`:_

The encoder settings per target. Video first, then the audio-only targets,
then subtitles -- which are the same ffmpeg but with no stream to encode,
only a text format to re-serialise.
Two arguments earn their place on every H.264 and VP9 target, and both are
there because of the same class of input: a GIF, a screen recording, a
phone video from an app that did not care.

```text
-pix_fmt yuv420p   a GIF arrives as a palette and a PNG-in-video as RGB,
                   and neither encoder will open with those. "Could not
                   open encoder before EOF" is what the user saw.
_EVEN              H.264 and VP9 need even dimensions, and a 601-pixel
                   screenshot is not something anybody should have to
                   know that about.
```

_At `"webm": ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-row-mt", "1",`:_

-row-mt lets VP9 use the threads it is given; without it the -threads
cap above mostly serialises the encode instead.

_At `"gif":  ["-vf", "fps=15,scale=480:-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]pa …`:_

A GIF built from the default 256-colour web palette bands badly on
anything filmed. palettegen/paletteuse spends one extra pass working out
the palette this particular clip needs, and it is the difference between
a usable GIF and a poster for why nobody uses GIFs.

_At `"mp3":  ["-vn", "-c:a", "libmp3lame", "-q:a", "2"],`:_

-vn on every audio target: it is what makes "extract the audio" the same
code path as "re-encode this MP3".

_At `"png":  ["-frames:v", "1", "-an"],`:_

A still target for a moving source means one frame -- the first one.
Only reachable from an animated GIF, APNG or WEBP, where "just the picture" is a reasonable thing to ask.

_At `_HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}`:_

---------- HDR ----------
A phone video shot in HDR -- HDR10 or HLG; 4K60 HDR10 is what a Galaxy S23 Ultra records -- stores brightness on a curve (PQ or HLG) that an
SDR file does not have. Copied as it is into any target above, all of them
8-bit and SDR, it plays grey and washed out. So an HDR source going to one
of them is tonemapped on the way: to linear light, BT.2020's colours into
BT.709's, and a tone curve (Hable) that keeps the highlights rather than
clipping them. It is not cheap at 4K, which is part of what jobs'
early stop is for.

It needs ffmpeg's zscale (libzimg). Without it the file converts as it
always did -- washed out, but converted.

### `_ffmpeg_has_filters()`

Whether this ffmpeg was built with every one of these filters. Asked
once per process.

- `_filters_seen.setdefault("", False)` — asked, even if nothing came back

### `_is_hdr()`

Whether the first video stream is on an HDR curve, from ffprobe.

### `_tonemapped()`

The same arguments, with the tonemap in the video filter -- after the
downscale for the small animated targets, where it costs a fraction of
what it would at 4K, and first everywhere else -- and the result labelled
as the BT.709 it now is.

_At `FIT_PIXELS = int(os.environ.get("CONVERT_VIDEO_FIT_PIXELS") or 2_900_000)`:_

---------- a big picture on a small machine ----------
Measured 2026-10-07 on a synthetic 3840x2160 clip with this bot's own ffmpeg
arguments, peak resident memory of the ffmpeg process:

```text
                 as it was      scaled to fit 1920x1920 first
HDR  -> mp4        1,334 MB          558 MB   (and 7.3 s -> 4.1 s)
HDR  -> webm       1,655 MB          667 MB   (54 s -> 15 s)
SDR  -> mp4          887 MB            -
SDR  -> webm       1,182 MB            -      (53 s)
```

On a 1 GB container the first two and the fourth are a kill, which is what
CV-NO-MEMORY has been (an S23 Ultra films 4K60 HDR, so it is not a
rare file). The 2.0.0 note said "converted at 1080p with the result saying
so" was the way out if it became common; this is that. What is brought down:

```text
- an HDR video over FIT_PIXELS, to any video target -- the tonemap works in
  32-bit float and its cost is the picture's area, so it runs after the
  scale;
- any video over FIT_PIXELS to WEBM, whose VP9 encoder is the other thing
  that does not fit at 4K.
```

MP4, MKV, MOV and AVI from a 4K picture that is not HDR keep their size: they
fit, if narrowly (the review of 2026-10-07 doubted "narrowly": 887 MB for
ffmpeg, plus the bot and the worker, is about the whole of a 1 GB container --
but 4K is what phones film, so it is left until a measurement says otherwise).
What no longer keeps its size is anything BIGGER than 4K, HDR or not: an 8K
or 5.7K clip going to MP4, MKV or MOV was never scaled, so it was always
killed for memory and refunded. Past HUGE_PIXELS every video target is
brought down. "Fit" is a 1920x1920 box, so a portrait phone video comes
out 1080x1920 and a 4:3 one 1920x1440; nothing is ever scaled up.
CONVERT_VIDEO_FIT_PIXELS=0 turns all of it off (a host with the memory).

### `_quarter_turn()`

Whether the player turns this stream a quarter (90 or 270 degrees) before
showing it, which swaps its width and its height.

A phone films a portrait clip as a landscape picture and writes the turn
next to it: a display matrix, which ffprobe reports as side data with a
`rotation`, and in older ffprobe (5.x and before) also as a `rotate` tag.
The width and height ffprobe prints are the STORED ones -- a portrait 4K
clip says 3840x2160 -- while ffmpeg turns the picture on its way in
(autorotate), so what it converts, and what the person sees, is
2160x3840. Confirmed 2026-10-08 on a clip written with -display_rotation.
The side data is asked first; the tag only when there is none, so a file
that carries both is not turned twice.

### `video_geometry()`

(width, height, is HDR) of the first video stream, from one ffprobe
call -- or None for no video stream, no ffprobe, or an answer that makes
no sense. The size is the DISPLAYED one: a clip stored sideways and turned
by a rotation flag is reported the way up it is shown and converted.

### `video_needs_fit()`

Whether this video is brought down to the 1920x1920 box for this
target. Pure.

### `fitted_size()`

What the box makes of this size: never larger, even numbers, same shape.

### `_fitted()`

The same arguments with the fit in front of whatever video filter they
already have -- before the tonemap, because that is the expensive part.

### `video_fit_note()`

What to tell somebody about a video before they pay: None, or
(which, width, height, fitted width, fitted height) with `which` being
"hdr" (HDR: every video target is brought down), "huge" (not HDR, but
bigger than 4K: every video target is brought down) or "big" (only WEBM
is).

### `_media_duration()`

How long the file plays, in seconds, from ffprobe -- or None, and the
progress bar simply does not appear.

### `_ffmpeg_with_progress()`

The same ffmpeg run, reading its -progress output as it goes. stderr goes
to a file rather than a pipe, so a chatty encoder can never fill a pipe
nobody is reading and stall; the time limit is the same one.

### `_reject_overlong_animation()`

An animation with more frames than MAX_FRAMES is refused up front.

It is a work limit rather than a size limit, and it belongs before the
invoice: a few hundred kilobytes of GIF can be an hour of encoding, and
finding that out after taking somebody's Stars is the wrong order.

_At `def zip_files(paths, out_path) -> None:`:_

---------- packing several outputs into one file ----------

### `zip_files()`

One page per entry, deflated.

A PDF with forty pages converted to PNG is forty files, and Telegram
sends one document per message. A zip is the only answer that arrives as
a single thing the user can do something with.

_At `def _out(work_dir, stem: str, ext: str) -> Path:`:_

---------- the dispatcher ----------

### `convert()`

Everything the bot converts, dispatched by what the source is.

`ocr_lang` and `ocr_pages` are for the conversions that read text
(ocr_convert.py) and nothing else: the person's choice of language, and
the number of pages the job was priced for.

`in_paths` is a list because one conversion -- pictures into a PDF -- takes
a set of files, and because "convert all of these to WEBP" is the same
question asked once per file. Every other conversion takes exactly one and
ignores the rest of the list.

The result is always a single file, because a Telegram message is a single
document. Where a conversion genuinely produces many (a PDF rendered to
images, a batch converted one by one) they are zipped and `Result.zipped`
says so, so the caption can.

- At `raise ConversionError("Text is read from one picture or PDF at a time, not from an album …`:
  An album is converted one picture at a time, and each would be read
  for the price of one: the menu does not offer it, and this does not take it.
- At `if src_ext in formats.ANIMATED_CAPABLE:`:
  The file settles which of the two a GIF, WEBP or APNG is. Asked here as
  well as in the menu, because the menu is a message the user could have
  been looking at for a quarter of an hour and the answer has to be the
  one the conversion is actually run under.
- At `reading = ocr_job_pages(src_ext, target_ext, ocr_pages, len(in_paths))`:
  Reading text. A picture is read whatever it is turned into; a PDF only
  when the job was priced for pages to read (ocr_job_pages, the function
  the quote asked) -- otherwise its TXT is today's, doc_convert's, with
  its own refusal for a PDF that has no text. Never more pages than the
  price counted: ocr_convert refuses a file that now has more.
- At `import office_convert`:
  Office files, and the targets only LibreOffice writes, go through LibreOffice (office_convert).
- At `convert_media_ffmpeg(in_path, target_ext, out_path)`:
  A still frame asked for a moving target -- mp4 from a one-frame
  GIF. ffmpeg is the one who can, and it is a legitimate request.

### `_convert_batch()`

Several files at once: one PDF, or one zip of individual results.

Only pictures arrive in batches -- Telegram albums are photos and videos,
and a set of videos is not something anybody wants merged. So the two
answers are "bind them into a document" and "do each of them", and the
second is a zip because the first would be a lie.
