"""Draws the Starkist logo and the small interface icons into assets/ui/.

Only needed when the artwork changes:  uv run python tools/make_assets.py
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "assets" / "ui"

DEEP = (11, 26, 34)
TILE_TOP = (27, 66, 80)
TILE_BOTTOM = (13, 35, 46)
GLASS = (79, 214, 184)
CORAL = (255, 111, 77)
FOAM = (236, 246, 243)
MUTED = (134, 163, 168)
TEXT = (230, 242, 239)

SS = 4  # supersampling factor


def _canvas(size: int) -> tuple[Image.Image, ImageDraw.ImageDraw, int]:
    n = size * SS
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img), n


def _finish(img: Image.Image, size: int) -> Image.Image:
    return img.resize((size, size), Image.LANCZOS)


def logo(size: int = 512) -> Image.Image:
    """A fishing float sitting in sound ripples, on a rounded deep-water tile."""
    n = size * SS
    # tile with a vertical gradient
    grad = Image.new("RGBA", (n, n))
    gd = ImageDraw.Draw(grad)
    for y in range(n):
        t = y / (n - 1)
        gd.line(
            [(0, y), (n, y)],
            fill=tuple(round(a + (b - a) * t) for a, b in zip(TILE_TOP, TILE_BOTTOM, strict=True)) + (255,),
        )
    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, n - 1, n - 1], radius=int(n * 0.23), fill=255)
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)

    cx, water_y = n * 0.5, n * 0.62
    # ripples: flattened rings spreading out from the float, fading with distance
    rings = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    rd = ImageDraw.Draw(rings)
    for i, (rx, alpha) in enumerate(((0.19, 255), (0.31, 170), (0.43, 90))):
        w, h = n * rx, n * rx * 0.34
        rd.ellipse(
            [cx - w, water_y - h, cx + w, water_y + h],
            outline=GLASS + (alpha,),
            width=int(n * (0.030 - i * 0.004)),
        )
    rings.putalpha(ImageChops.multiply(rings.getchannel("A"), mask))
    img = Image.alpha_composite(img, rings)

    # the float: stem, coral top half, foam bottom half; the waterline hides the lowest part
    d = ImageDraw.Draw(img)
    r = n * 0.155
    by = water_y - r * 0.45
    d.rounded_rectangle(
        [cx - n * 0.016, by - r - n * 0.15, cx + n * 0.016, by - r + n * 0.03],
        radius=n * 0.016,
        fill=FOAM,
    )
    d.ellipse([cx - n * 0.032, by - r - n * 0.185, cx + n * 0.032, by - r - n * 0.121], fill=CORAL)
    ball = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    bd = ImageDraw.Draw(ball)
    bd.ellipse([cx - r, by - r, cx + r, by + r], fill=FOAM)
    top = Image.new("L", (n, n), 0)
    ImageDraw.Draw(top).rectangle([0, 0, n, by], fill=255)
    coral = Image.new("RGBA", (n, n), CORAL + (255,))
    coral.putalpha(ImageChops.multiply(ball.getchannel("A"), top))
    ball = Image.alpha_composite(ball, coral)
    bd = ImageDraw.Draw(ball)
    bd.rectangle([cx - r, by - n * 0.011, cx + r, by + n * 0.011], fill=DEEP)
    # cut the float off where it meets the front of the innermost ripple
    cut = Image.new("L", (n, n), 255)
    w, h = n * 0.19, n * 0.19 * 0.34
    ImageDraw.Draw(cut).chord(
        [cx - w, water_y - h, cx + w, water_y + h], start=0, end=180, fill=0
    )
    ImageDraw.Draw(cut).rectangle([0, water_y + h, n, n], fill=0)
    ball.putalpha(ImageChops.multiply(ball.getchannel("A"), cut))
    img = Image.alpha_composite(img, ball)
    # redraw the near edge of the first ripple over the float
    front = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    ImageDraw.Draw(front).arc(
        [cx - w, water_y - h, cx + w, water_y + h], start=0, end=180, fill=GLASS + (255,),
        width=int(n * 0.030),
    )
    img = Image.alpha_composite(img, front)
    return _finish(img, size)


# -- small line icons ----------------------------------------------------------------------------


def _icon(draw_fn, color: tuple[int, int, int], size: int = 96) -> Image.Image:
    img, d, n = _canvas(size)
    draw_fn(d, n, color + (255,), max(2, int(n * 0.085)))
    return _finish(img, size)


def _round_line(d: ImageDraw.ImageDraw, pts: list[tuple[float, float]], c, w: int) -> None:
    d.line(pts, fill=c, width=w, joint="curve")
    for x, y in (pts[0], pts[-1]):
        d.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=c)


def i_window(d, n, c, w) -> None:
    d.rounded_rectangle([n * 0.12, n * 0.2, n * 0.88, n * 0.8], radius=n * 0.1, outline=c, width=w)
    d.line([(n * 0.12, n * 0.4), (n * 0.88, n * 0.4)], fill=c, width=w)


def i_key(d, n, c, w) -> None:
    d.rounded_rectangle([n * 0.14, n * 0.16, n * 0.86, n * 0.84], radius=n * 0.16, outline=c, width=w)
    _round_line(d, [(n * 0.36, n * 0.62), (n * 0.64, n * 0.62)], c, w)


def i_clock(d, n, c, w) -> None:
    d.ellipse([n * 0.12, n * 0.12, n * 0.88, n * 0.88], outline=c, width=w)
    _round_line(d, [(n * 0.5, n * 0.3), (n * 0.5, n * 0.5), (n * 0.65, n * 0.6)], c, w)


def i_wave(d, n, c, w) -> None:
    for x, half in ((0.18, 0.1), (0.34, 0.26), (0.5, 0.36), (0.66, 0.2), (0.82, 0.1)):
        _round_line(d, [(n * x, n * (0.5 - half)), (n * x, n * (0.5 + half))], c, w)


def i_note(d, n, c, w) -> None:
    _round_line(d, [(n * 0.42, n * 0.7), (n * 0.42, n * 0.2), (n * 0.78, n * 0.14), (n * 0.78, n * 0.6)], c, w)
    d.ellipse([n * 0.18, n * 0.58, n * 0.46, n * 0.84], fill=c)
    d.ellipse([n * 0.54, n * 0.48, n * 0.82, n * 0.74], fill=c)


def i_refresh(d, n, c, w) -> None:
    d.arc([n * 0.16, n * 0.16, n * 0.84, n * 0.84], start=40, end=330, fill=c, width=w)
    ax, ay = n * 0.5 + n * 0.34 * math.cos(math.radians(330)), n * 0.5 + n * 0.34 * math.sin(math.radians(330))
    d.polygon([(ax - n * 0.2, ay - n * 0.02), (ax + n * 0.08, ay - n * 0.16), (ax + n * 0.06, ay + n * 0.14)], fill=c)


def i_folder(d, n, c, w) -> None:
    d.line(
        [(n * 0.12, n * 0.3), (n * 0.12, n * 0.78), (n * 0.88, n * 0.78), (n * 0.88, n * 0.36),
         (n * 0.5, n * 0.36), (n * 0.4, n * 0.22), (n * 0.12, n * 0.22), (n * 0.12, n * 0.3)],
        fill=c, width=w, joint="curve",
    )


def i_plus(d, n, c, w) -> None:
    _round_line(d, [(n * 0.5, n * 0.2), (n * 0.5, n * 0.8)], c, w)
    _round_line(d, [(n * 0.2, n * 0.5), (n * 0.8, n * 0.5)], c, w)


def i_pin(d, n, c, w) -> None:
    d.polygon([(n * 0.34, n * 0.14), (n * 0.66, n * 0.14), (n * 0.6, n * 0.46), (n * 0.76, n * 0.6),
               (n * 0.24, n * 0.6), (n * 0.4, n * 0.46)], fill=c)
    _round_line(d, [(n * 0.5, n * 0.6), (n * 0.5, n * 0.88)], c, w)


def i_trash(d, n, c, w) -> None:
    _round_line(d, [(n * 0.16, n * 0.28), (n * 0.84, n * 0.28)], c, w)
    _round_line(d, [(n * 0.4, n * 0.16), (n * 0.6, n * 0.16)], c, w)
    d.line([(n * 0.26, n * 0.3), (n * 0.3, n * 0.84), (n * 0.7, n * 0.84), (n * 0.74, n * 0.3)],
           fill=c, width=w, joint="curve")


def i_play(d, n, c, w) -> None:
    d.polygon([(n * 0.28, n * 0.16), (n * 0.84, n * 0.5), (n * 0.28, n * 0.84)], fill=c)


def i_stop(d, n, c, w) -> None:
    d.rounded_rectangle([n * 0.22, n * 0.22, n * 0.78, n * 0.78], radius=n * 0.1, fill=c)


def i_compact(d, n, c, w) -> None:
    """Two chevrons pointing at each other: fold the window down to the strip."""
    _round_line(d, [(n * 0.28, n * 0.12), (n * 0.5, n * 0.36), (n * 0.72, n * 0.12)], c, w)
    _round_line(d, [(n * 0.28, n * 0.88), (n * 0.5, n * 0.64), (n * 0.72, n * 0.88)], c, w)


def i_expand(d, n, c, w) -> None:
    """Two chevrons pointing away from each other: open the full window again."""
    _round_line(d, [(n * 0.28, n * 0.36), (n * 0.5, n * 0.12), (n * 0.72, n * 0.36)], c, w)
    _round_line(d, [(n * 0.28, n * 0.64), (n * 0.5, n * 0.88), (n * 0.72, n * 0.64)], c, w)


ICONS = {
    "window": i_window, "key": i_key, "clock": i_clock, "wave": i_wave, "note": i_note,
    "refresh": i_refresh, "folder": i_folder, "plus": i_plus, "pin": i_pin, "trash": i_trash,
    "compact": i_compact, "expand": i_expand,
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    mark = logo(512)
    mark.save(OUT / "logo.png")
    mark.save(OUT / "starkist.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    for name, fn in ICONS.items():
        _icon(fn, MUTED).save(OUT / f"{name}.png")
        _icon(fn, TEXT).save(OUT / f"{name}_on.png")
    _icon(i_pin, CORAL).save(OUT / "pin_active.png")
    _icon(i_play, DEEP).save(OUT / "play.png")
    _icon(i_stop, TEXT).save(OUT / "stop.png")
    print(f"wrote {len(list(OUT.iterdir()))} files to {OUT}")


if __name__ == "__main__":
    main()
