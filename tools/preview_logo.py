"""Render a logo's ^GFA ink-bitmap conversion back to a PNG so it can be
eyeballed on screen before spending a physical label. Renders both
conversion methods (dither, threshold) at true print proportions -- a wide
or tall logo is fit and letterboxed exactly as it will be on the label, not
squashed into a square -- scaled up with nearest-neighbor so individual
printed dots stay visible.

Ported from the brady-i4311-printer PoC's scripts/preview_logo.py, updated
to preserve aspect ratio (see src/zpl_template.py's fit_logo_footprint).

Usage: python -m tools.preview_logo [logo_path] [square_side_px] [out_dir]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image

from src.logo import image_aspect_ratio, render_ink_bitmap, resolve_logo
from src.zpl_template import fit_logo_footprint

UPSCALE = 6
DEFAULT_LOGO_PATH = "assets/placeholder_logo.png"


def render_preview(logo_path: str, square_side_px: int, out_dir: Path) -> None:
    image = resolve_logo(logo_path)
    if image is None:
        print(f"Could not load {logo_path!r} -- nothing to preview", file=sys.stderr)
        raise SystemExit(1)

    aspect_ratio = image_aspect_ratio(image)
    size_px = fit_logo_footprint(aspect_ratio, square_side_px)

    for method in ("dither", "threshold"):
        bitmap = render_ink_bitmap(image, size_px=size_px, method=method)
        preview = Image.new("1", (bitmap.width, bitmap.height), 1)  # 1 = white
        for y, row in enumerate(bitmap.rows):
            for x in range(bitmap.width):
                bit = (row[x // 8] >> (7 - (x % 8))) & 1
                preview.putpixel((x, y), 0 if bit else 1)
        out_path = out_dir / f"logo_preview_{method}.png"
        preview.resize(
            (bitmap.width * UPSCALE, bitmap.height * UPSCALE), Image.NEAREST
        ).save(out_path)
        print(f"wrote {out_path} ({size_px[0]}x{size_px[1]} at print size, aspect ratio {aspect_ratio:.2f})")


if __name__ == "__main__":
    logo_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_LOGO_PATH
    square_side_px = int(sys.argv[2]) if len(sys.argv) > 2 else 132
    out_dir = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("docs")
    out_dir.mkdir(parents=True, exist_ok=True)
    render_preview(logo_path, square_side_px, out_dir)
