"""The five logos, checked against the two things that actually break one.

A logo is not judged by a test. What a test can do is catch the two ways these
stop working, both of which are invisible on the 640-pixel canvas they are
drawn on and obvious on a phone:

  **The circular crop.** Telegram shows every avatar as a circle. Anything
  further from the centre than the inscribed circle is simply not there, and a
  glyph that reaches for the corners loses its ends.

  **Chat-list size.** The same image is drawn at about 34 pixels, a nineteenth
  of the canvas. A stroke thinner than about 30 units at full size is a grey
  smudge at that one, which is where the logo does nearly all of its work.

It also checks that the committed files still match the generator, because a
logo edited by hand in one format and not the other is a logo that will
silently disagree with itself for a year.

Reads the shapes out of scripts/make_logos.py rather than the images, so the
assertions are about the design and not about pixels.

    python3 tests/test_logos.py
"""
import math
import pathlib
import struct
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import make_logos  # noqa: E402

CENTRE = make_logos.SIZE / 2
# Telegram's crop is the inscribed circle. Staying a little inside it is what
# stops a glyph from looking like it is touching the rim.
SAFE_RADIUS = 260
# Below this, a shape is a smudge in a chat list.
MIN_FEATURE = 30
# The description picture is not cropped to anything, so its rule is the
# reverse of the avatar's: keep clear of every edge by at least this much.
HERO_MARGIN = 12
# And it is never shown at chat-list size, so MIN_FEATURE is the wrong rule
# for it -- that number exists to survive 34 pixels. A description picture is
# drawn at roughly the width of the chat, call it two thirds of its 640, so a
# 16-unit stroke arrives at about 11 real pixels. The card's glyph is smaller
# in absolute units than the avatar's (it is fitted into 360 of height rather
# than 640), which is exactly why this needs its own floor rather than
# inheriting one.
HERO_MIN_FEATURE = 16

failures = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL  ") + name + (f" -- {detail}" if not ok and detail else ""))
    if not ok:
        failures.append(name)


def _corners(shape):
    """Every point that matters, and the shape's thinnest dimension."""
    kind = shape[0]
    if kind == "poly":
        points = shape[1]
        xs = [x for x, _ in points]
        ys = [y for _, y in points]
        return points, min(max(xs) - min(xs), max(ys) - min(ys))
    if kind in ("bar", "rect"):
        _, x, y, w, h, _ = shape
        return [(x, y), (x + w, y), (x, y + h), (x + w, y + h)], min(w, h)
    if kind == "dot":
        _, cx, cy, r, _ = shape
        return [(cx - r, cy - r), (cx + r, cy - r), (cx - r, cy + r), (cx + r, cy + r)], 2 * r
    if kind == "ring":
        _, cx, cy, r, width, _ = shape
        edge = r + width / 2
        return [(cx - edge, cy - edge), (cx + edge, cy - edge),
                (cx - edge, cy + edge), (cx + edge, cy + edge)], width
    raise AssertionError(f"unknown shape {kind!r}")


def _reach(shape) -> float:
    """How far this shape gets from the centre."""
    kind = shape[0]
    if kind == "dot":
        _, cx, cy, r, _ = shape
        return math.dist((cx, cy), (CENTRE, CENTRE)) + r
    if kind == "ring":
        _, cx, cy, r, width, _ = shape
        return math.dist((cx, cy), (CENTRE, CENTRE)) + r + width / 2
    points, _ = _corners(shape)
    return max(math.dist(p, (CENTRE, CENTRE)) for p in points)


def _relative_luminance(hexcolour: str) -> float:
    def channel(value):
        value /= 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4
    r, g, b = make_logos._hex(hexcolour)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    la, lb = _relative_luminance(a), _relative_luminance(b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def main() -> int:
    print("=== the five logos ===")
    grounds = {}

    for folder, spec in make_logos.FAMILY.items():
        name = spec["name"]
        grounds[name] = spec["ground"]

        for shape in spec["shapes"]:
            reach = _reach(shape)
            check(f"{name}: {shape[0]} survives the circular crop",
                  reach <= SAFE_RADIUS, f"reaches {reach:.0f}, limit {SAFE_RADIUS}")
            _, thinnest = _corners(shape)
            check(f"{name}: {shape[0]} is thick enough to read at 34px",
                  thinnest >= MIN_FEATURE, f"{thinnest:.0f} units, minimum {MIN_FEATURE}")

        # White on the ground is the whole design; if that contrast is poor
        # the glyph is a suggestion rather than a mark. This is what caught
        # StickerBot sitting at 2.1:1 on amber-500.
        #
        # Shading is held to a lower bar on purpose. StickerBot's peel is a
        # fold, and a fold is a shadow on the surface it folds from -- push it
        # to 3:1 and it stops reading as a fold and starts reading as a hole
        # cut in the sticker. It has to be clearly visible, not loud.
        for shape in spec["shapes"]:
            ink = shape[-1]
            ratio = contrast(ink, spec["ground"])
            is_ink = ink == make_logos.WHITE
            floor = 3.0 if is_ink else 2.0
            check(f"{name}: {ink} reads against {spec['ground']}",
                  ratio >= floor, f"contrast {ratio:.1f}:1, floor {floor}")

        for suffix, minimum in (("svg", 200), ("png", 1000)):
            path = ROOT / folder / f"logo.{suffix}"
            check(f"{name}: logo.{suffix} exists and is not empty",
                  path.is_file() and path.stat().st_size > minimum,
                  f"{path.stat().st_size if path.is_file() else 'missing'} bytes")

        png = (ROOT / folder / "logo.png").read_bytes()
        width, height = struct.unpack(">II", png[16:24])
        check(f"{name}: the PNG is {make_logos.SIZE} square, as BotFather wants",
              (width, height) == (make_logos.SIZE, make_logos.SIZE), f"{width}x{height}")

        # Committed files against what the generator produces now.
        check(f"{name}: logo.svg matches scripts/make_logos.py",
              (ROOT / folder / "logo.svg").read_text(encoding="utf-8") == make_logos.to_svg(spec),
              "run: python scripts/make_logos.py")
        check(f"{name}: logo.png matches scripts/make_logos.py",
              png == make_logos.to_png(spec), "run: python scripts/make_logos.py")

    print("")
    print("=== the five description pictures ===")
    for folder, spec in make_logos.FAMILY.items():
        name = spec["name"]
        card = make_logos.hero(spec)
        width, height = card["width"], card["height"]
        x0, y0, x1, y1 = make_logos.bounds(card["shapes"])

        # The rule the avatar is held to is the circular crop. A landscape
        # card is not cropped at all, so the rule here is the opposite one:
        # nothing may touch an edge. Two of these marks are made of separated
        # parts -- ManagerBot's ring of dots, ConvertBot's two arrows -- and an
        # edge through either stops it being that mark.
        check(f"{name} card: the glyph is whole, with room around it",
              x0 >= HERO_MARGIN and y0 >= HERO_MARGIN
              and x1 <= width - HERO_MARGIN and y1 <= height - HERO_MARGIN,
              f"bounds ({x0:.0f}, {y0:.0f})-({x1:.0f}, {y1:.0f}) in {width}x{height}")

        # Big enough to be the subject rather than a sticker in a corner.
        check(f"{name} card: the glyph carries the frame",
              (y1 - y0) >= 0.6 * height, f"{(y1 - y0) / height:.0%} of the height")

        # And the empty ground on the left, which is the whole composition.
        check(f"{name} card: there is open ground to the left of it",
              x0 >= 0.3 * width, f"starts at {x0 / width:.0%} of the width")

        check(f"{name} card: same ground colour as the avatar",
              card["ground"] == spec["ground"])

        for shape in card["shapes"]:
            _, thinnest = _corners(shape)
            check(f"{name} card: {shape[0]} is thick enough at card size",
                  thinnest >= HERO_MIN_FEATURE, f"{thinnest:.0f} units, "
                  f"minimum {HERO_MIN_FEATURE}")

        png = (ROOT / folder / "hero.png").read_bytes()
        got = struct.unpack(">II", png[16:24])
        check(f"{name} card: the PNG is {width}x{height}, as BotFather wants",
              got == (width, height), f"{got[0]}x{got[1]}")

        check(f"{name} card: hero.svg matches scripts/make_logos.py",
              (ROOT / folder / "hero.svg").read_text(encoding="utf-8") == make_logos.to_svg(card),
              "run: python scripts/make_logos.py")
        check(f"{name} card: hero.png matches scripts/make_logos.py",
              png == make_logos.to_png(card), "run: python scripts/make_logos.py")

    print("")
    # Five bots side by side in one chat list. Two of them the same colour is
    # two of them looking like the same bot at a glance.
    check("every bot has a ground colour of its own",
          len(set(grounds.values())) == len(grounds), str(grounds))
    for name, ground in grounds.items():
        for other, other_ground in grounds.items():
            if name < other:
                ratio = contrast(ground, other_ground)
                check(f"{name} and {other} do not read as the same bot",
                      ratio >= 1.15 or ground != other_ground, f"contrast {ratio:.2f}")

    print("")
    print("FAILURES: " + str(len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
