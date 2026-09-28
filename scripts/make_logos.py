#!/usr/bin/env python3
"""Draw the family's five logos, as SVG and as PNG.

    python scripts/make_logos.py

Writes `logo.svg` and `logo.png` into each bot's folder, which is what each
README shows.
The PNG is 640x640, which is what @BotFather wants for a profile photo.

WHY THE GEOMETRY IS WRITTEN ONCE AND RENDERED TWICE
    An SVG is the thing to keep -- it is text, it diffs, and it scales. A PNG
    is the thing Telegram actually accepts. Drawing them separately would
    guarantee they drift, so each logo is a list of plain shapes below and
    there are two renderers, neither of which knows anything about logos.

WHAT A TELEGRAM PROFILE PHOTO ACTUALLY LOOKS LIKE
    A circle. Telegram crops every avatar to one and shows it at about 40px
    in a chat list, so two rules follow and both are load-bearing:

      * The background is full-bleed. A rounded-square badge -- the obvious
        app-icon instinct -- has its corners cropped away, and what is left is
        a circle with four bites out of it.
      * The glyph lives inside the middle 70%, and is built from a handful of
        thick strokes. Anything finer than about 40 units at this scale
        disappears at chat-list size, which is where the logo does its work.

    Both are checked by tests/test_logos.py rather than by eye.

EVERYTHING HERE IS ORIGINAL GEOMETRY
    Circles, rectangles, triangles and thick lines, placed by hand. No traced
    artwork, no icon set, no font -- which is also why no glyph is a letter.
    The bots' own TERMS.md tells users not to build packs out of other
    people's work, and shipping a logo lifted from an icon pack while saying
    that would be the kind of thing nobody forgives twice.
"""
import math
import pathlib
import struct
import zlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SIZE = 640          # what BotFather wants
SUPERSAMPLE = 4     # drawn at 4x and shrunk, which is where the edges come from

WHITE = "#FFFFFF"

# Each logo: a flat ground and the shapes on top of it. Coordinates are in a
# 0-640 space with the origin top-left, matching the SVG viewBox.
#
# The ground is flat on purpose. The first version had a darker wedge across
# the bottom-right corner for depth, which looked like an app icon and did two
# bad things at avatar size: it cut a diagonal through every glyph, and on
# StickerBot it happened to be the same colour as the peel, so the peel
# vanished. Depth is decoration; contrast is the thing that still works at
# 40 pixels.
FAMILY = {
    "sticker_bot": {
        "name": "StickerBot",
        "ground": "#D97706",
        # A square with one corner turned back: the shape of a sticker. The
        # fold is a third tone, darker than the ground, so that it reads as a
        # fold rather than as a bite taken out of the square.
        "shapes": [
            ("poly", [(196, 186), (454, 186), (454, 372), (376, 454), (196, 454)], WHITE),
            ("poly", [(376, 454), (454, 372), (376, 372)], "#78350F"),
        ],
    },
    "convert_bot": {
        "name": "ConvertBot",
        "ground": "#7C3AED",
        # Two arrows passing each other: this becomes that, and back.
        #
        # The first attempt had thin bars close together with small heads, and
        # at chat-list size the heads vanished and the two bars merged into an
        # equals sign. Thicker bars, a wide gap between them, and heads half
        # again as tall as the bar they end -- the head is the part that has to
        # survive, because it is the only thing that says "arrow".
        "shapes": [
            ("bar", 195, 234, 200, 44, WHITE),
            ("poly", [(383, 202), (459, 256), (383, 310)], WHITE),
            ("bar", 245, 362, 200, 44, WHITE),
            ("poly", [(257, 330), (181, 384), (257, 438)], WHITE),
        ],
    },
    "downloader_bot": {
        "name": "DownloaderBot",
        "ground": "#0D9488",
        # An arrow coming down onto a line: the universal "save this".
        #
        # The proportions are the whole design. A short wide head sitting
        # straight on a nearby bar reads as a cup on a saucer -- which is what
        # the first attempt looked like at any size. The head is narrower and
        # deeper now, and the bar is further away: the gap is what turns two
        # shapes into an arrow arriving somewhere.
        # The shaft is a square-ended rect, not a rounded bar. A rounded cap
        # on a narrow stem that flares into a triangle is the silhouette of a
        # light bulb, which is exactly what the first two attempts drew. It is
        # also short and thick rather than long and thin, so the head's
        # shoulders clearly stick out on both sides -- the shoulders are what
        # make it an arrow rather than a shape tapering to a point.
        "shapes": [
            ("rect", 286, 170, 68, 122, WHITE),
            ("poly", [(230, 288), (410, 288), (320, 402)], WHITE),
            ("bar", 210, 450, 220, 38, WHITE),
        ],
    },
    "anon_bot": {
        "name": "AnonBot",
        "ground": "#475569",
        # A speech bubble with nobody in it. The message arrives; the sender
        # does not, which is the whole bot in one shape.
        #
        # The tail's base sits INSIDE the ring's stroke band, not in the hollow
        # middle of it. Started any higher, the tail crosses the stroke and the
        # hollow shows through the join as a notch -- which is what the first
        # version did, and it read as a rendering fault rather than a bubble.
        "shapes": [
            ("ring", 320, 296, 126, 34, WHITE),
            ("poly", [(278, 404), (352, 404), (298, 486)], WHITE),
            ("dot", 320, 296, 30, WHITE),
        ],
    },
    "manager_bot": {
        "name": "ManagerBot",
        "ground": "#1F2937",
        # One in the middle, four around it: the family as it actually is.
        # The centre is amber rather than white because ManagerBot is the one
        # that is not like the others -- it is private and it is the watcher.
        "shapes": [
            ("ring", 320, 320, 138, 32, WHITE),
            ("dot", 320, 182, 40, WHITE),
            ("dot", 458, 320, 40, WHITE),
            ("dot", 320, 458, 40, WHITE),
            ("dot", 182, 320, 40, WHITE),
            ("dot", 320, 320, 52, "#F59E0B"),
        ],
    },
}


# ---------------------------------------------------------------------------
# SVG
# ---------------------------------------------------------------------------

def to_svg(spec: dict) -> str:
    width, height = _size_of(spec)
    label = spec.get("label", f'{spec["name"]} logo')
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" '
        f'aria-label="{label}">',
        f'  <title>{spec["name"]}</title>',
        f'  <rect width="{width}" height="{height}" fill="{spec["ground"]}"/>',
    ]
    for shape in spec["shapes"]:
        kind = shape[0]
        if kind == "poly":
            _, points, fill = shape
            path = " ".join(f"{x} {y}" for x, y in points)
            out.append(f'  <polygon points="{path}" fill="{fill}"/>')
        elif kind == "bar":
            _, x, y, w, h, fill = shape
            out.append(f'  <rect x="{x}" y="{y}" width="{w}" height="{h}" '
                       f'rx="{h / 2}" fill="{fill}"/>')
        elif kind == "rect":
            _, x, y, w, h, fill = shape
            out.append(f'  <rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill}"/>')
        elif kind == "dot":
            _, cx, cy, r, fill = shape
            out.append(f'  <circle cx="{cx}" cy="{cy}" r="{r}" fill="{fill}"/>')
        elif kind == "ring":
            _, cx, cy, r, width, fill = shape
            out.append(f'  <circle cx="{cx}" cy="{cy}" r="{r}" fill="none" '
                       f'stroke="{fill}" stroke-width="{width}"/>')
        else:
            raise ValueError(f"unknown shape {kind!r}")
    out.append("</svg>")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# PNG, without a drawing library
# ---------------------------------------------------------------------------
# Pillow is already a dependency of two bots, but not of this repo's tooling,
# and a logo generator that only runs where Pillow happens to be installed is
# a logo generator that stops running. The shapes here are a handful of
# polygons and circles; rasterising them by hand is fifty lines, and the
# supersampling that antialiases them is the same fifty lines run at 4x.

def _hex(colour: str) -> tuple:
    colour = colour.lstrip("#")
    return tuple(int(colour[i:i + 2], 16) for i in (0, 2, 4))


def _blank(width: int, height: int, colour: tuple) -> list:
    return [[colour] * width for _ in range(height)]


def _size_of(spec: dict) -> tuple:
    """A logo is SIZE square; a hero card carries its own width and height."""
    return spec.get("width", SIZE), spec.get("height", SIZE)


def _fill_poly(canvas, points, colour, scale):
    """Even-odd scanline fill. Good enough for convex and simple shapes,
    which is all any of these are."""
    pts = [(x * scale, y * scale) for x, y in points]
    ys = [p[1] for p in pts]
    for y in range(max(0, int(min(ys))), min(len(canvas), int(max(ys)) + 1)):
        crossings = []
        for i in range(len(pts)):
            (x1, y1), (x2, y2) = pts[i], pts[(i + 1) % len(pts)]
            if (y1 <= y < y2) or (y2 <= y < y1):
                crossings.append(x1 + (y - y1) * (x2 - x1) / (y2 - y1))
        crossings.sort()
        width = len(canvas[0])
        for i in range(0, len(crossings) - 1, 2):
            for x in range(max(0, int(crossings[i])), min(width, int(crossings[i + 1]) + 1)):
                canvas[y][x] = colour


def _fill_disc(canvas, cx, cy, r, colour, scale, inner=0.0):
    cx, cy, r, inner = cx * scale, cy * scale, r * scale, inner * scale
    for y in range(max(0, int(cy - r)), min(len(canvas), int(cy + r) + 1)):
        dy = y - cy
        span = math.sqrt(max(r * r - dy * dy, 0))
        x_from, x_to = int(cx - span), int(cx + span)
        for x in range(max(0, x_from), min(len(canvas[0]), x_to + 1)):
            if inner and (x - cx) ** 2 + dy * dy < inner * inner:
                continue
            canvas[y][x] = colour


def _fill_bar(canvas, x, y, w, h, colour, scale):
    """A rectangle with semicircular ends, which is what rx=h/2 draws."""
    radius = h / 2
    _fill_poly(canvas, [(x + radius, y), (x + w - radius, y),
                        (x + w - radius, y + h), (x + radius, y + h)], colour, scale)
    _fill_disc(canvas, x + radius, y + radius, radius, colour, scale)
    _fill_disc(canvas, x + w - radius, y + radius, radius, colour, scale)


def to_png(spec: dict) -> bytes:
    scale = SUPERSAMPLE
    # frame_w/frame_h, not width/height: the ring branch below unpacks a
    # stroke width out of its shape tuple, and a plain `width` here is
    # silently overwritten by it. The symptom is a 32-pixel-wide PNG for the
    # two bots whose logos contain a ring, and nothing at all for the others.
    frame_w, frame_h = _size_of(spec)
    canvas = _blank(frame_w * scale, frame_h * scale, _hex(spec["ground"]))

    for shape in spec["shapes"]:
        kind = shape[0]
        if kind == "poly":
            _fill_poly(canvas, shape[1], _hex(shape[2]), scale)
        elif kind == "bar":
            _, x, y, w, h, fill = shape
            _fill_bar(canvas, x, y, w, h, _hex(fill), scale)
        elif kind == "rect":
            _, x, y, w, h, fill = shape
            _fill_poly(canvas, [(x, y), (x + w, y), (x + w, y + h), (x, y + h)],
                       _hex(fill), scale)
        elif kind == "dot":
            _, cx, cy, r, fill = shape
            _fill_disc(canvas, cx, cy, r, _hex(fill), scale)
        elif kind == "ring":
            _, cx, cy, r, width, fill = shape
            _fill_disc(canvas, cx, cy, r + width / 2, _hex(fill), scale,
                       inner=r - width / 2)

    # Box-filter down to the finished size. This is the antialiasing.
    rows = []
    for y in range(frame_h):
        row = bytearray()
        for x in range(frame_w):
            r = g = b = 0
            for dy in range(scale):
                for dx in range(scale):
                    pr, pg, pb = canvas[y * scale + dy][x * scale + dx]
                    r += pr
                    g += pg
                    b += pb
            n = scale * scale
            row += bytes((r // n, g // n, b // n))
        rows.append(bytes(row))

    raw = b"".join(b"\x00" + row for row in rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", frame_w, frame_h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


# ---------------------------------------------------------------------------
# The description picture
# ---------------------------------------------------------------------------
# The other thing an empty chat shows. Above the Start button Telegram can
# display a landscape picture as well as the description text, and it is the
# largest single piece of screen a stranger sees before deciding whether to
# press anything. All five were blank.
#
# Same ground, same glyph, wider frame -- no new artwork, because the mark is
# the thing a person is being asked to recognise and a second design per bot
# would be two things to keep in step instead of one.
#
# NO TEXT ON IT. The description sits directly underneath in the reader's own
# language, and the family is trilingual; a picture repeating it in English
# would be worse than one that only says which bot this is. It would also
# need a font, and the whole file exists on the rule that nothing here is
# traced, licensed or borrowed.
#
# WHY THE GLYPH IS NOT CROPPED
#     The plan for this said "oversized and bled off one edge", which is the
#     ordinary way to make a banner feel confident, and it is wrong for these
#     five. Two of the marks are made of separated parts -- ManagerBot's ring
#     of four dots, ConvertBot's two passing arrows -- and an edge through
#     either stops it being that mark rather than cropping it. A rule that
#     flatters a square and destroys a constellation is not a rule.
#
#     So the glyph is whole, sized to about seventy per cent of the frame
#     height, and set right of centre. The empty ground to its left is the
#     composition: it is what stops the card reading as a logo that has been
#     stretched, and it is the same shape of space the avatar has around it.
#     tests/test_logos.py checks all three of those rather than trusting them.

HERO_W, HERO_H = 640, 360   # what BotFather asks for
HERO_HEIGHT_SHARE = 0.72    # how much of the frame's height the glyph fills
HERO_WIDTH_SHARE = 0.42     # and the widest it may get, for a wide mark
HERO_CENTRE_X = 0.62        # right of centre, leaving the ground on the left


def _shape_points(shape) -> list:
    """Every extreme point of a shape, which is all a bounding box needs."""
    kind = shape[0]
    if kind == "poly":
        return list(shape[1])
    if kind in ("bar", "rect"):
        _, x, y, w, h, _ = shape
        return [(x, y), (x + w, y + h)]
    if kind == "dot":
        _, cx, cy, r, _ = shape
        return [(cx - r, cy - r), (cx + r, cy + r)]
    if kind == "ring":
        _, cx, cy, r, width, _ = shape
        edge = r + width / 2
        return [(cx - edge, cy - edge), (cx + edge, cy + edge)]
    raise ValueError(f"unknown shape {kind!r}")


def bounds(shapes) -> tuple:
    """(x0, y0, x1, y1) around every shape in a glyph."""
    points = [p for shape in shapes for p in _shape_points(shape)]
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    return min(xs), min(ys), max(xs), max(ys)


def _moved(shape, k: float, dx: float, dy: float):
    """One shape, scaled about the origin by k and then translated."""
    def at(x, y):
        return (x * k + dx, y * k + dy)

    kind = shape[0]
    if kind == "poly":
        return ("poly", [at(x, y) for x, y in shape[1]], shape[2])
    if kind in ("bar", "rect"):
        _, x, y, w, h, fill = shape
        nx, ny = at(x, y)
        return (kind, nx, ny, w * k, h * k, fill)
    if kind == "dot":
        _, cx, cy, r, fill = shape
        ncx, ncy = at(cx, cy)
        return ("dot", ncx, ncy, r * k, fill)
    if kind == "ring":
        _, cx, cy, r, width, fill = shape
        ncx, ncy = at(cx, cy)
        return ("ring", ncx, ncy, r * k, width * k, fill)
    raise ValueError(f"unknown shape {kind!r}")


def hero(spec: dict) -> dict:
    """The same logo, laid out for a 640x360 card."""
    x0, y0, x1, y1 = bounds(spec["shapes"])
    glyph_w, glyph_h = x1 - x0, y1 - y0
    k = min(HERO_HEIGHT_SHARE * HERO_H / glyph_h,
            HERO_WIDTH_SHARE * HERO_W / glyph_w)
    dx = HERO_CENTRE_X * HERO_W - k * (x0 + x1) / 2
    dy = HERO_H / 2 - k * (y0 + y1) / 2
    return {
        "name": spec["name"],
        "label": f'{spec["name"]}',
        "ground": spec["ground"],
        "width": HERO_W,
        "height": HERO_H,
        "shapes": [_moved(shape, k, dx, dy) for shape in spec["shapes"]],
    }


def main() -> int:
    for folder, spec in FAMILY.items():
        target = ROOT / folder
        (target / "logo.svg").write_text(to_svg(spec), encoding="utf-8")
        png = to_png(spec)
        (target / "logo.png").write_bytes(png)

        card = hero(spec)
        (target / "hero.svg").write_text(to_svg(card), encoding="utf-8")
        hero_png = to_png(card)
        (target / "hero.png").write_bytes(hero_png)

        print(f"{folder}: logo {SIZE}x{SIZE} ({len(png) // 1024} KB), "
              f"hero {HERO_W}x{HERO_H} ({len(hero_png) // 1024} KB)  {spec['ground']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
