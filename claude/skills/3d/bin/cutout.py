#!/usr/bin/env python3
"""
Turn a photograph into a figure that can stand on a page.

Two jobs, and the second is the one that is always skipped. Removing the
background is a solved problem and a model does it. What the model leaves is a
silhouette that runs into the edge of the frame the photographer chose, so the
subject ends on a straight vertical line that no ground can explain - and that
line is the whole difference between a cutout standing on a surface and a
photograph lying on one.

So this reads the alpha, finds which edges the silhouette actually reaches, and
feathers only those. An edge the subject never touches keeps its cut: hair
stays hair, a jaw stays a jaw. The bottom of a portrait almost always touches,
which is why the shoulders dissolve into the ground instead of ending on the
crop.

    cutout.py <photo> --out <dir> --name <stem> [--widths 900,560]

Writes `<stem>.webp` at the first width, `<stem>-<w>.webp` at each of the rest,
a lossless `<stem>.png` master, and two previews composited on a light and a
dark ground so the edges can be checked against both before anything ships.

Needs `rembg` and `pillow`. `numpy` comes with rembg.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# How far in from a touched edge the alpha is taken to nothing, as a fraction of
# that side's length. The bottom gets more room than the sides because a torso
# leaving the frame downward is read as continuing, and a torso ending sideways
# is read as cut.
BOTTOM_FEATHER = 0.28
SIDE_FEATHER = 0.10

# Where down the frame the side feather starts to apply at all. Above this the
# sides keep their own cut, which is what protects the head: on a portrait the
# only thing near the left and right edges up there is hair, and feathered hair
# reads as a photograph out of focus.
SIDE_FEATHER_FROM = 0.45
SIDE_FEATHER_TO = 0.62

# What counts as the silhouette reaching an edge. A model leaves a few points of
# alpha in the corners of almost every cut, and treating those as contact
# feathers an edge nothing is standing on.
EDGE_ALPHA = 8
EDGE_RUN = 0.02


def smoothstep(t):
    import numpy as np

    u = np.clip(t, 0.0, 1.0)
    return u * u * (3.0 - 2.0 * u)


def ramp(t, lo, hi):
    return smoothstep((t - lo) / (hi - lo))


def touched(alpha, axis_slice, length):
    """Whether the silhouette actually reaches this edge, rather than grazing it."""
    import numpy as np

    return int(np.count_nonzero(axis_slice > EDGE_ALPHA)) > max(2, int(length * EDGE_RUN))


def feather(image, verbose=True):
    """Take the alpha to nothing at every frame edge the silhouette runs into."""
    import numpy as np
    from PIL import Image

    array = np.array(image).astype(np.float32)
    alpha = array[..., 3] / 255.0
    height, width = alpha.shape
    raw = array[..., 3]

    hits = {
        "left": touched(raw, raw[:, 0], height),
        "right": touched(raw, raw[:, -1], height),
        "bottom": touched(raw, raw[-1, :], width),
        "top": touched(raw, raw[0, :], width),
    }
    if verbose:
        reached = ", ".join(name for name, hit in hits.items() if hit) or "none"
        print(f"  silhouette reaches: {reached}")

    down = np.linspace(0, 1, height)[:, None]
    across = np.linspace(0, 1, width)[None, :]
    mask = np.ones_like(alpha)

    if hits["bottom"]:
        mask *= 1.0 - ramp(down, 1.0 - BOTTOM_FEATHER, 1.0)
    if hits["top"]:
        mask *= ramp(down, 0.0, BOTTOM_FEATHER)

    # The sides fade in only over the lower part of the frame, so the feather is
    # applied where a torso leaves the picture and not where a head sits.
    if hits["left"] or hits["right"]:
        depth = ramp(down, SIDE_FEATHER_FROM, SIDE_FEATHER_TO)
        side = np.ones_like(across)
        if hits["left"]:
            side = np.minimum(side, ramp(across, 0.0, SIDE_FEATHER))
        if hits["right"]:
            side = np.minimum(side, 1.0 - ramp(across, 1.0 - SIDE_FEATHER, 1.0))
        mask *= side * depth + (1.0 - depth)

    array[..., 3] = np.clip(alpha * mask, 0.0, 1.0) * 255.0
    out = Image.fromarray(array.astype(np.uint8))
    box = out.getchannel("A").getbbox()
    return out.crop(box) if box else out


def main():
    parser = argparse.ArgumentParser(description="Cut a subject out and feather its frame edges.")
    parser.add_argument("photo", help="The source photograph.")
    parser.add_argument("--out", required=True, help="Directory to write into.")
    parser.add_argument("--name", required=True, help="Filename stem, e.g. jane-doe-figure.")
    parser.add_argument(
        "--widths",
        default="900,560",
        help="Comma-separated output widths. The first is the unsuffixed file.",
    )
    parser.add_argument("--quality", type=int, default=80, help="WebP quality (default 80).")
    parser.add_argument(
        "--model", default="isnet-general-use", help="rembg model (default isnet-general-use)."
    )
    parser.add_argument(
        "--no-feather", action="store_true", help="Keep the model's own edges everywhere."
    )
    args = parser.parse_args()

    try:
        from PIL import Image
        from rembg import new_session, remove
    except ImportError as missing:
        sys.exit(f"needs rembg and pillow: pip install rembg pillow ({missing})")

    source = Path(args.photo).expanduser()
    if not source.exists():
        sys.exit(f"no such photo: {source}")
    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    widths = [int(w) for w in args.widths.split(",") if w.strip()]
    if not widths:
        sys.exit("--widths named none")

    print(f"reading {source}")
    photo = Image.open(source).convert("RGBA")
    print(f"  {photo.width}x{photo.height}")

    # Alpha matting is what keeps hair from becoming a stencil. It costs a few
    # seconds and it is the difference between an edge and an outline.
    cut = remove(
        photo,
        session=new_session(args.model),
        alpha_matting=True,
        alpha_matting_foreground_threshold=250,
        alpha_matting_background_threshold=15,
        alpha_matting_erode_size=8,
    )
    box = cut.getchannel("A").getbbox()
    if box:
        cut = cut.crop(box)
    print(f"  cut to {cut.width}x{cut.height}")

    figure = cut if args.no_feather else feather(cut)
    master = out_dir / f"{args.name}.png"
    figure.save(master)

    written = []
    for index, width in enumerate(widths):
        height = round(figure.height * width / figure.width)
        stem = args.name if index == 0 else f"{args.name}-{width}"
        path = out_dir / f"{stem}.webp"
        figure.resize((width, height), Image.LANCZOS).save(
            path, "WEBP", quality=args.quality, method=6
        )
        written.append((path, width, height))

    # Both grounds, because an edge that is invisible on paper can be a halo on
    # a dark slab and the site draws on both.
    for label, ground in (("light", (246, 246, 245, 255)), ("dark", (10, 10, 10, 255))):
        preview = Image.new("RGBA", figure.size, ground)
        preview.alpha_composite(figure)
        preview.convert("RGB").save(out_dir / f"{args.name}-on-{label}.png")

    print(f"\nmaster  {master}  ({figure.width}x{figure.height})")
    for path, width, height in written:
        print(f"served  {path}  {width}x{height}  {path.stat().st_size // 1024}KB")
    print(f"check   {out_dir}/{args.name}-on-light.png and -on-dark.png before shipping")
    print(f"\nsrcSet  {' , '.join(f'/{p.name} {w}w' for p, w, _ in written)}")


if __name__ == "__main__":
    main()
