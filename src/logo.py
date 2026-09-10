"""Convert a logo PNG into a ZPL ^GFA monochrome graphic field for embedding
directly in a label (no printer-side storage needed).

Ported from the brady-i4311-printer PoC's logo.py. The saturation-aware ink
metric is the PoC's key finding and is kept verbatim, including this
docstring, so it doesn't get "simplified" back into plain luminance later:

Why not plain grayscale threshold for the ink metric itself: naive
luminance (L = 0.3R+0.59G+0.11B) scores a saturated, brightly-colored
wordmark as LIGHTER than a mid-gray element even when the color is clearly
"more ink" to the eye, so a mid threshold can partially or fully drop
colored artwork while keeping neutral grays crisp -- the opposite of what
a general-purpose logo converter needs, since it must work for arbitrary
third-party logos, not just neutral ones. Instead we score "ink" as
distance-from-white on whichever channel is most saturated (min(R,G,B)
channel), which scores saturated colors as strong ink (their weakest
channel is far from white) alongside neutral grays.

Swapping the logo is meant to be the easiest thing another adopter does:
drop an image into the bind-mounted assets/ folder, any Pillow-readable
format, any size, with or without alpha, and it just works -- no code
changes, no image pre-processing. `resolve_logo()` and `LogoCache` below
are what make that true: a bad or missing logo must never cost someone a
tag (print logo-less with a warning instead), and aspect ratio is
preserved rather than squashed into a square (see zpl_template.py's
fit_logo_footprint).
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

from PIL import Image, ImageChops

logger = logging.getLogger(__name__)

DitherMethod = str  # "dither" | "threshold"


@dataclass(frozen=True)
class MonoBitmap:
    width: int
    height: int
    rows: list[bytes]  # one packed byte-row per pixel row, MSB-first, bit=1 -> black/print

    @property
    def bytes_per_row(self) -> int:
        return len(self.rows[0]) if self.rows else 0


def _ink_intensity(rgb_image: Image.Image) -> Image.Image:
    """Return an 'L' image where higher value = more ink coverage needed.

    Uses per-pixel distance-from-white on the most-saturated channel rather
    than plain luminance, so saturated colors aren't underweighted relative
    to neutral grays (see module docstring).
    """
    white = Image.new("RGB", rgb_image.size, (255, 255, 255))
    diff = ImageChops.difference(rgb_image, white)  # each channel = 255 - original
    r, g, b = diff.split()
    return ImageChops.lighter(ImageChops.lighter(r, g), b)


def load_logo(path: str) -> Image.Image:
    return Image.open(path)


def resolve_logo(path: str | None) -> Image.Image | None:
    """Safely load a logo image, never raising: an empty path means "no
    logo" by design (4.6.1), and any load failure (missing file, unreadable
    format) is logged as a warning and treated the same way -- a logo
    problem must never cost someone a tag."""
    if not path:
        return None
    try:
        image = Image.open(path)
        image.load()  # force the read now, so a truncated/corrupt file
        return image  # fails here rather than later during conversion
    except (OSError, ValueError) as exc:
        logger.warning("could not load logo %r (%s) -- printing without a logo", path, exc)
        return None


def image_aspect_ratio(image: Image.Image) -> float:
    width, height = image.size
    return width / height


def render_ink_bitmap(
    image: Image.Image,
    size_px: tuple[int, int],
    method: DitherMethod = "threshold",
    threshold: int = 96,
) -> MonoBitmap:
    """Convert an (RGBA or RGB) logo image to a packed 1-bit ink bitmap.

    size_px: (width, height) target size in dots.
    method: "threshold" (flat cutoff; default) or "dither" (Floyd-Steinberg).
            Both stay available since which reproduces a given logo's fine
            detail better is artwork-specific -- see tools/preview_logo.py.
    threshold: cutoff (0-255) used only when method == "threshold".
    """
    if size_px[0] <= 0 or size_px[1] <= 0:
        raise ValueError(f"size_px must be positive, got {size_px}")

    resized = image.convert("RGBA").resize(size_px, Image.LANCZOS)
    # Composite onto white first: many logo sources have a transparent
    # background, and un-composited alpha would otherwise read as "no ink"
    # everywhere.
    white_bg = Image.new("RGBA", resized.size, (255, 255, 255, 255))
    composited = Image.alpha_composite(white_bg, resized).convert("RGB")

    ink = _ink_intensity(composited)  # 0 = white/no ink, 255 = full ink

    if method == "dither":
        # Pillow's convert("1") dithers toward black for low input values,
        # so invert ink -> "L for convert" and invert the result back.
        inverted = ink.point(lambda p: 255 - p)
        as_mode1 = inverted.convert("1")  # 0 = black (was high ink), 255 = white
        get_ink = lambda x, y: 1 if as_mode1.getpixel((x, y)) == 0 else 0
    elif method == "threshold":
        get_ink = lambda x, y: 1 if ink.getpixel((x, y)) >= threshold else 0
    else:
        raise ValueError(f"Unknown method {method!r}, expected 'dither' or 'threshold'")

    width, height = size_px
    bytes_per_row = (width + 7) // 8
    rows: list[bytes] = []
    for y in range(height):
        row = bytearray(bytes_per_row)
        for x in range(width):
            if get_ink(x, y):
                row[x // 8] |= 0x80 >> (x % 8)
        rows.append(bytes(row))

    return MonoBitmap(width=width, height=height, rows=rows)


def bitmap_to_gf_field(bitmap: MonoBitmap) -> str:
    """Render a MonoBitmap as a ZPL ^GFA (ASCII-hex, uncompressed) field body,
    e.g. "^GFA,4032,4032,32,<hex...>". Caller wraps with ^FO/^FS placement.
    """
    total_bytes = bitmap.bytes_per_row * bitmap.height
    hex_data = "".join(row.hex().upper() for row in bitmap.rows)
    return f"^GFA,{total_bytes},{total_bytes},{bitmap.bytes_per_row},{hex_data}"


def convert_image_to_gf_field(
    image: Image.Image,
    size_px: tuple[int, int],
    method: DitherMethod = "threshold",
    threshold: int = 96,
) -> str:
    bitmap = render_ink_bitmap(image, size_px, method=method, threshold=threshold)
    return bitmap_to_gf_field(bitmap)


def convert_logo_to_gf_field(
    path: str,
    size_px: tuple[int, int],
    method: DitherMethod = "threshold",
    threshold: int = 96,
) -> str:
    image = load_logo(path)
    return convert_image_to_gf_field(image, size_px, method=method, threshold=threshold)


class LogoCache:
    """Holds the converted ^GFA field for a logo, re-reading the source
    file from disk only every `refresh_seconds` (default: 5 minutes, per
    the project's logo-refresh setting) rather than on every label --
    render_ink_bitmap is a pure-Python per-pixel loop, and the logo is
    identical on every tag, so re-running it per print is wasted work.

    Re-checking on a timer (rather than a filesystem watch) lets an
    adopter replace the logo file in the bind-mounted assets/ folder and
    have it picked up without restarting the container. A load failure at
    any point (missing file, corrupt image) falls back to printing
    logo-less rather than raising -- see resolve_logo().
    """

    def __init__(
        self,
        path: str | None,
        method: DitherMethod = "threshold",
        threshold: int = 96,
        refresh_seconds: float = 300.0,
    ):
        self.path = path
        self.method = method
        self.threshold = threshold
        self.refresh_seconds = refresh_seconds
        self._image: Image.Image | None = None
        self._checked_once = False
        self._last_checked: float = float("-inf")
        self._last_mtime: float | None = None
        self._gf_field_cache: dict[tuple[int, int], str] = {}

    def _current_mtime(self) -> float | None:
        if not self.path:
            return None
        try:
            return os.path.getmtime(self.path)
        except OSError:
            return None

    def _maybe_reload(self, now: float) -> None:
        if self._checked_once and now - self._last_checked < self.refresh_seconds:
            return
        self._last_checked = now
        mtime = self._current_mtime()
        if self._checked_once and mtime == self._last_mtime:
            return  # unchanged since last check -- keep the cached conversions
        self._checked_once = True
        self._last_mtime = mtime
        self._image = resolve_logo(self.path)
        self._gf_field_cache = {}  # source changed (or first load) -- drop stale conversions

    def ensure_loaded(self, now: float | None = None) -> None:
        """Trigger the reload check without needing a target size yet.
        Callers that need `aspect_ratio` before they can compute a layout
        (and therefore before they know what size to ask get_gf_field()
        for) must call this first -- aspect_ratio alone won't load
        anything on a cache that's never been touched."""
        self._maybe_reload(time.time() if now is None else now)

    @property
    def aspect_ratio(self) -> float | None:
        return image_aspect_ratio(self._image) if self._image is not None else None

    def get_gf_field(self, size_px: tuple[int, int], now: float | None = None) -> str | None:
        """Return the cached ^GFA field for size_px, converting (and
        caching) only if this is the first call, the refresh interval has
        elapsed, or size_px hasn't been rendered yet. Returns None if there
        is no usable logo (empty path or load failure)."""
        self._maybe_reload(time.time() if now is None else now)
        if self._image is None:
            return None
        if size_px not in self._gf_field_cache:
            self._gf_field_cache[size_px] = convert_image_to_gf_field(
                self._image, size_px, method=self.method, threshold=self.threshold
            )
        return self._gf_field_cache[size_px]
