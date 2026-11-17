# `raw_convert.py`

[↑ convert_bot](README.md) · [code](raw_convert.py)

Camera RAW: CR2, CR3, NEF, ARW, DNG, RAF, ORF and RW2 to JPG, PNG, WEBP, AVIF,
HEIC or TIFF.

A photo straight off a camera -- or a phone: an S23 Ultra shoots 50 and
200 MP DNG in Expert RAW -- is a mosaic of single-colour sensor readings and
what the camera decided about white balance. Turning it into a picture is
LibRaw's job (`rawpy`, MIT, with LibRaw's LGPL/CDDL inside, in a wheel for Linux
and Windows, CR3 included). What is asked of it here is what a person who did
not ask for anything expects: the camera's own white balance, no automatic
brightening, sRGB out, AHD demosaicing and the camera's own orientation.
Pillow takes it from there, through the same table every other still picture is
written by, so each of the six targets is the one the rest of the bot makes.
TIFF is the exception, and on purpose: it comes out at 16 bits per channel, for
somebody who means to edit it (Pillow cannot write that, so the file is written
here -- uncompressed, which is what an editor wants to open).

\## The limit that matters is memory

Measured 2026-10-07 on synthetic DNGs, peak resident memory of the decode in a
process of its own (8-bit output; 16-bit adds about 20%):

```text
 24 MP full   346 MB        24 MP half   147 MB
 50 MP full   684 MB        50 MP half   271 MB
100 MP full 1,334 MB       100 MP half   509 MB
                           200 MP half   984 MB
```

About 13 MB a megapixel, which is why "the full picture up to 100 MP" in
PLANNED.md was wrong for a 1 GB container: a 50 MP photo is most of the host
before it is encoded to anything. So:

```text
* up to FULL_MP (40) the full picture;
* past it, LibRaw's half-size decode -- a quarter of the pixels, no
  demosaicing at all -- and the person is told, before they pay, what size
  they will get;
* past MAX_MP (110) it is refused with a sentence and nothing is charged: even
  the half-size decode of a 200 MP file takes a gigabyte.
```

The megapixel count is read from the file's own metadata, without decoding it
(`inspect`), so the menu, the quote and the refusal all know it the way they
know a JPEG's.

### `RawInfo`

- `width: int` — the picture's own size, what a person would call it
- `half: bool` — will it be decoded at half size
- `out_megapixels: float` — what comes out: what the size estimates are about

### `_open()`

The file opened by LibRaw. `unpack=False` reads the header only:
`rawpy.imread` always unpacks -- the whole raw image decoded into memory,
about 200 MB for 100 MP -- which a question about the size does not need
(review, 2026-10-08).

### `_mp()`

A megapixel count for a sentence: whole numbers for photos, two places
for the small ones a test or a thumbnail can be.

### `_sizes()`

The picture's own width and height, after the camera's turn.

- `if int(getattr(raw.sizes, "flip", 0) or 0) in (5, 6):` — turned a quarter: the sides swap

### `inspect()`

Size and plan from the file's header, without decoding a pixel. Raises
ConversionError for a file that is not a raw photo.

### `megapixels()`

The size the way jobs.describe_image gives it for a picture -- None
for anything that cannot be read -- but the OUTPUT size, because that is
what the estimates of what comes out are about.

## 16 bits per channel

### `_srgb_profile()`

Pillow carries lcms; with it the TIFF says which colours its numbers are,
which is what an editor asks about an untagged 16-bit file. Without it the
file is still valid and is read as sRGB, which is what it is.

### `_write_tiff16()`

An uncompressed baseline TIFF of an (height, width, 3) uint16 array: one
strip, little-endian, written without making a second copy of the pixels.

- `tags = [` — (tag, type, count, value) -- value is an int, or bytes for anything over 4 bytes
- `(273, LONG, 1, None),` — strip offset, filled in below

### `describe()`

inspect(), or None for a file that cannot be read -- for the menu, which
would rather say nothing than refuse a file the converter might still take.
