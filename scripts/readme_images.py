"""Round the corners of README images, matching docs/architecture.svg (radius 1.5% of width).

GitHub strips CSS from READMEs, so the rounding is baked into the files: screenshots become
WebP with transparent corners, which also keeps them smaller than the JPEGs they replace. The
overview animation is an animated WebP with the same mask (see scripts.record_overview).

    uv run python -m scripts.readme_images docs/screenshots/curio-*.jpg   # -> .webp, deletes .jpg

`scripts.capture_readme` and `scripts.record_overview` call these helpers directly.
"""

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

RADIUS_SHARE = 18 / 1200  # the architecture diagram's rx="18" on a 1200-wide canvas
SUPERSAMPLE = 4


def corner_mask(size: tuple[int, int]) -> Image.Image:
    """An anti-aliased rounded-rectangle alpha mask (255 inside, 0 in the corners)."""
    width, height = size
    radius = max(4, round(width * RADIUS_SHARE))
    big = Image.new("L", (width * SUPERSAMPLE, height * SUPERSAMPLE), 0)
    ImageDraw.Draw(big).rounded_rectangle(
        (0, 0, width * SUPERSAMPLE - 1, height * SUPERSAMPLE - 1),
        radius * SUPERSAMPLE,
        fill=255,
    )
    return big.resize(size, Image.LANCZOS)


def round_corners(image: Image.Image) -> Image.Image:
    rounded = image.convert("RGBA")
    rounded.putalpha(corner_mask(rounded.size))
    return rounded


def save_rounded_webp(image: Image.Image, path: Path) -> Path:
    path = path.with_suffix(".webp")
    round_corners(image).save(path, "WEBP", quality=86, method=6)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--keep", action="store_true", help="Keep the source files.")
    args = parser.parse_args()
    for source in args.images:
        with Image.open(source) as image:
            target = save_rounded_webp(image, source)
        if not args.keep and source != target:
            source.unlink()
        print(f"{source} -> {target} ({target.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
