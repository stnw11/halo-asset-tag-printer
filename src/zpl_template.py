"""ZPL II label template for the asset tag print.

Ported from the brady-i4311-printer PoC's zpl_template.py. Layout math is
unchanged except for the QR sizing fix below (the PoC hardcoded QR version
1's module count and silently clipped longer tag values -- see
`_qr_module_count`) and the text-legibility floor in `compute_layout`.

Kept separate from networking (printer_client.py) and image conversion
(logo.py) so darkness/speed/dpi/layout math is unit-testable without a
socket or Pillow.

CONFIRMED: the i4311 supports ZPL II (Zebra emulation mode) -- this is
Brady's documented compatibility mode for i-series printers.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, fields
from pathlib import Path

import segno
import yaml

from .config import PrinterConfig

MM_PER_INCH = 25.4
TEXT_CHAR_WIDTH_RATIO = 0.6    # approx avg glyph width / height for ZPL font 0
TEXT_BOLD_WIDTH_RATIO = 0.85   # font width param relative to height -- pushes
    # toward a bolder look without badly distorting character shapes; a
    # font-metric approximation, not a per-deployment tunable, so it stays
    # a constant rather than moving into config/layout.yaml.


@dataclass(frozen=True)
class LayoutConfig:
    """Config-first layout tunables -- see config/layout.example.yaml.
    Defaults match the values the reference PoC arrived at against its
    physical unit; existing callers that don't pass a LayoutConfig get
    identical geometry to before this became configurable."""
    outer_margin_in: float = 0.08     # blank margin at the left/right label edges
    element_gap_in: float = 0.04      # blank gap between logo/text/QR
    # Symmetric top/bottom margins by default -- logo, text, and QR all
    # end up vertically centered on the label as a result (each is sized to
    # fill the vertical zone, so equal margins above and below that zone
    # means equal margins above and below the label as a whole). An earlier
    # version of this file had these asymmetric (top 0.02", bottom 0.05")
    # to compensate for a specific printer unit's leading-edge calibration
    # offset -- that was overfit to that one unit and produced a
    # logo/QR-touching-the-top look on this deployment's actual printer, so
    # the default reverted to symmetric. If your printer has a real
    # leading-edge offset, re-introduce an asymmetric top_margin_in /
    # bottom_margin_in here based on a live print, the same way the
    # original PoC did -- just don't assume its specific values transfer.
    # NOTE: on 0.5"-tall stock there is very little vertical slack to give.
    # At 300 dpi, a QR version-2 symbol (17+ char tag values) needs a
    # 125-dot working zone just to clear the minimum legible module size
    # (min_qr_module_size_mm below) -- these margins are already close to
    # that ceiling. Increasing them further for extra feed-drift buffer
    # will push v2-length tags below the legible floor and raise
    # LayoutError. If that trade is wanted, raise these and accept that
    # long tag values need a larger label; otherwise leave as-is and add
    # drift buffer via outer_margin_in instead, which doesn't compete with
    # QR sizing.
    top_margin_in: float = 0.04
    bottom_margin_in: float = 0.04
    logo_target_min_in: float = 0.40  # logo's target square footprint
    logo_target_max_in: float = 0.45
    # Font size is fixed, designed around this many characters -- NOT
    # scaled per-tag to the actual value's length. Confirmed via a live
    # print: scaling font size to the actual character count means a
    # short tag (few characters) computes a much larger font than a long
    # one, and for a very short value the font can grow to fill the
    # entire vertical zone with zero centering slack -- which, combined
    # with any text_baseline_offset_ratio, pushed text physically off the
    # bottom of the label. A fixed size avoids that failure mode entirely
    # and gives every label the same, consistent look regardless of tag
    # length. Set this to comfortably cover your longest realistic tag
    # value; a tag longer than this raises LayoutError (see below) rather
    # than silently shrinking the font or letting ZPL's ^FB truncate it.
    text_target_char_count: int = 9
    # Positive shifts the human-readable text down, as a fraction of the
    # rendered font height (not a fixed dot count) -- ZPL's ^A0 height
    # parameter is a bounding box that reserves descender space below the
    # baseline even for all-caps/digit text that never uses it, so the
    # visible glyph sits high within its own box. That gap scales with
    # font size, so the correction has to as well, or it's wrong for every
    # tag length except the one it was tuned against. There's no way to
    # compute the right ratio from config alone; it has to come from a
    # live print. 0.0 until tuned.
    text_baseline_offset_ratio: float = 0.0

    # --- QR sizing ---------------------------------------------------
    # ^BQ mode 2 auto-selects the QR *version* (module count) from the
    # payload length, encoding mode, and error-correction level -- it is
    # NOT fixed at version 1's 21 modules. The fix: compute the actual
    # version with segno (a pure-Python QR encoder) before laying out, so
    # the magnification sent to the printer is derived from the symbol it
    # will actually draw. This assumes segno and the printer's firmware
    # independently arrive at the same minimum version for the same input,
    # which ISO/IEC 18004 makes deterministic given equal data/mode/ECC --
    # verify this on physical media for a v2-length tag, not just in tests.
    qr_error_correction: str = "Q"    # H/Q/M/L; matches the "M<ecc>," field-data prefix.
        # Quartile (~25% recoverable) over Medium: these tags live on
        # handled hardware. Costs QR capacity (v1: 16 alphanumeric chars at
        # Q vs 20 at M) -- lower to "M" if your tag convention runs long.
    min_qr_module_size_mm: float = 0.4   # phone cameras get unreliable
        # scanning below roughly this at normal handling distance.
    min_text_font_dots_at_300dpi: int = 24  # ~0.08in / ~5.75pt; dpi-scaled
        # below. Below this, rendering raises a clear error instead of
        # printing an illegible tag.


DEFAULT_LAYOUT_CONFIG = LayoutConfig()

_LAYOUT_CONFIG_FIELD_NAMES = {f.name for f in fields(LayoutConfig)}


def load_layout_config(path: str | Path) -> LayoutConfig:
    """Load config/layout.yaml. Unknown keys are rejected (likely a typo --
    a silently-ignored layout setting is a confusing way to fail); missing
    keys fall back to LayoutConfig's defaults."""
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    raw = data.get("layout") or {}
    unknown = set(raw) - _LAYOUT_CONFIG_FIELD_NAMES
    if unknown:
        raise ValueError(f"{path}: unknown layout setting(s): {sorted(unknown)}")
    return LayoutConfig(**raw)


def _qr_module_count(payload: str, error_correction: str) -> int:
    """Return the actual module count (per side) of the QR symbol the
    printer will draw for `payload` at the given ECC level -- the version
    ^BQ mode 2 will auto-select, not an assumed constant."""
    qr = segno.make(payload, error=error_correction.lower(), micro=False, boost_error=False)
    width, _height = qr.symbol_size(border=0)
    return width


def _min_qr_magnification(dpi: int, min_module_size_mm: float) -> int:
    dots_per_mm = dpi / MM_PER_INCH
    return max(1, math.ceil(min_module_size_mm * dots_per_mm))


def fit_logo_footprint(aspect_ratio: float, square_side: int) -> tuple[int, int]:
    """Fit an image of the given aspect ratio (width/height) inside a
    square_side x square_side box, preserving aspect ratio ("contain" fit).

    Returns (rendered_w, rendered_h), both <= square_side. A wide image
    (aspect_ratio > 1) uses the full width and is letterboxed vertically.
    A tall image (aspect_ratio < 1) uses the full height and renders
    narrower than the square -- that's the horizontal space the caller
    should reclaim for the text field. aspect_ratio == 1 (the PoC's
    square-logo assumption) reduces exactly to (square_side, square_side).
    """
    scale_divisor = max(aspect_ratio, 1.0)
    rendered_w = max(1, round(aspect_ratio * square_side / scale_divisor))
    rendered_h = max(1, round(square_side / scale_divisor))
    return rendered_w, rendered_h


@dataclass(frozen=True)
class Box:
    x: int
    y: int
    w: int
    h: int

    def within(self, width: int, height: int) -> bool:
        return 0 <= self.x and 0 <= self.y and self.x + self.w <= width and self.y + self.h <= height


@dataclass(frozen=True)
class Layout:
    canvas_width_px: int   # ZPL ^PW -- horizontal / left-right axis
    canvas_height_px: int  # ZPL ^LL -- vertical axis
    logo: Box
    text: Box
    text_font_height: int
    text_font_width: int
    text_render_y: int  # ^FO y for the text field, vertically centering the glyph block within `text` (text.y is the box top, used for bounds-checking)
    qr: Box
    qr_magnification: int


class LayoutError(ValueError):
    """Raised when the configured label size can't fit the required elements."""


def in_to_dots(inches: float, dpi: int) -> int:
    return int(round(inches * dpi))


def compute_layout(
    cfg: PrinterConfig,
    logo_aspect_ratio: float | None = 1.0,
    layout_config: LayoutConfig = DEFAULT_LAYOUT_CONFIG,
) -> Layout:
    """logo_aspect_ratio: width/height of the source logo image. Defaults
    to 1.0 (square), matching the PoC's original assumption, so existing
    callers that don't pass it get identical geometry. Pass the real
    source image's aspect ratio to preserve it instead of squashing wide
    or tall logos, or None when there is no logo at all (freed entirely to
    the text field -- see logo.resolve_logo).

    Font size is fixed (LayoutConfig.text_target_char_count), not scaled
    to cfg.human_text's actual length -- see the field's docstring. This
    means there's no separate text_len parameter: cfg.human_text's real
    length is only used to check it actually fits at that fixed size."""
    dpi = cfg.dpi
    dim_a = in_to_dots(cfg.label_width_in, dpi)
    dim_b = in_to_dots(cfg.label_height_in, dpi)
    # LABEL_WIDTH_IN/LABEL_HEIGHT_IN describe the physical stock as quoted
    # on the roll. The logo/text/QR sit side by side "left to right" with
    # the logo sized to ~0.4-0.45in and only a small margin top/bottom --
    # that only fits if the LONGER edge is the horizontal/left-right axis
    # (ZPL ^PW) and the SHORTER edge is the tight vertical axis (ZPL ^LL).
    # Resolved by magnitude rather than by field name, so it's correct
    # regardless of which of LABEL_WIDTH_IN/LABEL_HEIGHT_IN is configured
    # larger.
    label_w = max(dim_a, dim_b)
    label_h = min(dim_a, dim_b)
    side_margin = max(1, in_to_dots(layout_config.outer_margin_in, dpi))
    gap = max(1, in_to_dots(layout_config.element_gap_in, dpi))
    top_margin = max(1, in_to_dots(layout_config.top_margin_in, dpi))
    bottom_margin = max(1, in_to_dots(layout_config.bottom_margin_in, dpi))

    # Logo/text/QR all share this vertical zone: top-anchored at
    # top_margin, sized to leave bottom_margin of clearance below (see the
    # asymmetric-margin comment above the constants).
    vertical_budget = label_h - top_margin - bottom_margin
    if vertical_budget <= 0:
        raise LayoutError(
            f"Label's short edge ({label_h}px at {dpi} DPI) leaves no room "
            f"for margins ({top_margin}px top + {bottom_margin}px bottom)"
        )

    # Logo: target square footprint 0.40-0.45in, clamped to whatever fits
    # within the vertical zone. The source image is fit inside that square
    # preserving aspect ratio -- a tall logo renders narrower than the
    # square and the freed width goes to the text field below; a wide logo
    # uses the full width and is letterboxed vertically. No logo at all
    # (logo_aspect_ratio=None) reserves zero footprint entirely.
    if logo_aspect_ratio is None:
        logo_box = Box(x=side_margin, y=top_margin, w=0, h=0)
        text_x = side_margin
    else:
        logo_target_side = min(in_to_dots(layout_config.logo_target_max_in, dpi), vertical_budget)
        logo_target_side = max(logo_target_side, min(in_to_dots(layout_config.logo_target_min_in, dpi), vertical_budget))
        rendered_w, rendered_h = fit_logo_footprint(logo_aspect_ratio, logo_target_side)
        band_y = top_margin + (vertical_budget - logo_target_side) // 2
        logo_y = band_y + (logo_target_side - rendered_h) // 2
        logo_box = Box(x=side_margin, y=logo_y, w=rendered_w, h=rendered_h)
        text_x = logo_box.x + logo_box.w + gap

    # QR: square, magnification targets the *minimum* legible module size
    # (min_qr_module_size_mm) rather than maximizing to fill the vertical
    # zone -- a bigger-than-necessary QR isn't "more correct," it's just
    # visually oversized relative to the logo/text next to it. Using the
    # floor directly also means QR size stays consistent across tag
    # lengths instead of shrinking only once a longer payload forces a
    # bigger version: v1 and v2 payloads render at the same magnification
    # whenever both comfortably clear the floor. Magnification is derived
    # from the QR symbol's *actual* version for this payload, not an
    # assumed one (see _qr_module_count above).
    qr_modules = _qr_module_count(cfg.barcode_value, layout_config.qr_error_correction)
    min_magnification = _min_qr_magnification(dpi, layout_config.min_qr_module_size_mm)
    qr_magnification = min(10, min_magnification)
    qr_side = qr_magnification * qr_modules
    if qr_side > vertical_budget:
        raise LayoutError(
            f"QR payload {cfg.barcode_value!r} ({len(cfg.barcode_value)} chars) "
            f"needs a {qr_modules}-module QR symbol, which doesn't fit in the "
            f"{vertical_budget}px vertical zone at {dpi} DPI even at the minimum "
            f"legible magnification ({qr_magnification}, for the "
            f"{layout_config.min_qr_module_size_mm}mm minimum module size). "
            "Shorten the tag naming convention, use a larger label, or lower "
            "the QR error-correction level."
        )
    qr_x = label_w - side_margin - qr_side
    qr_y = top_margin + (vertical_budget - qr_side) // 2
    qr_box = Box(x=qr_x, y=qr_y, w=qr_side, h=qr_side)

    # Text: fills whatever horizontal space is left between logo and QR
    # (text_x was set above -- either after the logo's actual rendered
    # footprint, or at side_margin if there's no logo at all).
    # Horizontal centering within that space is done in ZPL itself via
    # ^FB (field block, center-justified) in build_zpl rather than
    # estimated here, since exact glyph widths for ZPL's proportional
    # font 0 aren't available to us -- ^FB lets the printer do it exactly.
    text_w = qr_box.x - gap - text_x
    text_h = vertical_budget
    text_y = top_margin

    if text_w <= 0:
        raise LayoutError(
            f"Computed text field width is {text_w}px (label's long edge, "
            f"{label_w}px at {dpi} DPI, is too narrow for logo+text+QR side "
            "by side). Increase the larger of LABEL_WIDTH_IN/LABEL_HEIGHT_IN, "
            "reduce DPI, or shrink the QR/logo targets."
        )

    # Fixed size, designed around text_target_char_count -- not scaled to
    # cfg.human_text's actual length (see LayoutConfig.text_target_char_count).
    target_chars = max(layout_config.text_target_char_count, 1)
    font_h_by_height = text_h
    font_h_by_width = int(text_w / target_chars / TEXT_CHAR_WIDTH_RATIO)
    font_height = max(1, min(font_h_by_height, font_h_by_width))

    min_font_dots = round(layout_config.min_text_font_dots_at_300dpi * dpi / 300)
    if font_height < min_font_dots:
        raise LayoutError(
            f"Tag value {cfg.human_text!r} ({len(cfg.human_text)} chars) would "
            f"render at {font_height}px font height, below the {min_font_dots}px "
            f"legibility floor at {dpi} DPI. Shorten the tag naming convention "
            "or use a larger label -- this is a naming-convention problem, not "
            "a printer problem."
        )
    font_width = max(1, int(font_height * TEXT_BOLD_WIDTH_RATIO))

    # Since font size no longer scales to the actual tag's length, a tag
    # longer than text_target_char_count needs an explicit check -- ^FB's
    # single-line mode (see build_zpl) truncates silently rather than
    # wrapping or erroring, so an unchecked over-length tag would print a
    # cut-off value with no warning. Same glyph-width estimate used for
    # the fixed-size calculation above, applied to the real text this time.
    estimated_text_width = int(len(cfg.human_text) * font_height * TEXT_CHAR_WIDTH_RATIO)
    if estimated_text_width > text_w:
        raise LayoutError(
            f"Tag value {cfg.human_text!r} ({len(cfg.human_text)} chars) is "
            f"longer than the fixed font size accommodates "
            f"(text_target_char_count={layout_config.text_target_char_count}). "
            "Shorten the tag naming convention, raise text_target_char_count "
            "(shrinks the font for every tag), or use a larger label."
        )

    # Vertical centering: ZPL has no built-in vertical-center for a field,
    # so approximate it by centering the font's height parameter within
    # the vertical zone, then apply the live-tuned baseline correction
    # (see LayoutConfig.text_baseline_offset_ratio) for the printer's
    # actual glyph rendering, which isn't pixel-identical to the ^A0
    # height parameter (reserved descender space the glyph doesn't use).
    #
    # Clamped to stay within the vertical zone regardless of the offset:
    # a short tag value (few characters) can legitimately compute a
    # font_height that fills the *entire* zone with zero centering slack
    # (width unconstrained -> height is the only limit) -- confirmed via a
    # live print with a 3-character tag, where an unclamped offset pushed
    # the text 45 dots past the bottom of a 150-dot label. Without this
    # clamp, text_baseline_offset_ratio silently produces an off-label,
    # cut-off print for any tag short enough to hit that ceiling.
    centered_y = top_margin + (vertical_budget - font_height) // 2
    offset_dots = round(font_height * layout_config.text_baseline_offset_ratio)
    max_render_y = top_margin + vertical_budget - font_height
    text_render_y = max(top_margin, min(centered_y + offset_dots, max_render_y))

    text_box = Box(x=text_x, y=text_y, w=text_w, h=text_h)

    layout = Layout(
        canvas_width_px=label_w,
        canvas_height_px=label_h,
        logo=logo_box,
        text=text_box,
        text_font_height=font_height,
        text_font_width=font_width,
        text_render_y=text_render_y,
        qr=qr_box,
        qr_magnification=qr_magnification,
    )

    for name, box in (("logo", layout.logo), ("text", layout.text), ("qr", layout.qr)):
        if not box.within(label_w, label_h):
            raise LayoutError(f"{name} box {box} falls outside label bounds {label_w}x{label_h}")

    return layout


# --- ZPL field data escaping -------------------------------------------
# ^ starts a ZPL format command, ~ starts a ZPL control command, and _ is
# the ^FH hex-escape marker itself -- any of these (or non-printable-ASCII
# bytes) inside a ^FD value must be hex-escaped via ^FH, or they'll either
# break parsing or print literally wrong.
_SPECIAL_CHARS = frozenset("^~_")


def _needs_escape(ch: str) -> bool:
    return ch in _SPECIAL_CHARS or not (0x20 <= ord(ch) <= 0x7E)


def zpl_field_data(text: str) -> str:
    """Return a ZPL field-data fragment (^FD..., or ^FH^FD... with hex
    escapes) safely encoding `text` for use inside a ^FO...^FS block."""
    if not any(_needs_escape(ch) for ch in text):
        return f"^FD{text}"
    escaped = "".join(f"_{ord(ch):02X}" if _needs_escape(ch) else ch for ch in text)
    return f"^FH^FD{escaped}"


def build_zpl(
    cfg: PrinterConfig,
    logo_gf_field: str | None,
    layout: Layout | None = None,
    layout_config: LayoutConfig = DEFAULT_LAYOUT_CONFIG,
) -> str:
    """Assemble the full ZPL II label program as a string.

    logo_gf_field: a ready-to-embed "^GFA,..." field body, e.g. from
    logo.convert_logo_to_gf_field(), or None to omit the logo entirely (an
    empty/missing LOGO_ASSET -- the layout should have been computed with
    logo_aspect_ratio=None to match, so the text field already reclaimed
    that space). Networking-independent and pure (given the same inputs it
    always returns the same ZPL).
    """
    layout = layout or compute_layout(cfg, layout_config=layout_config)

    media_tracking = "^MNY" if cfg.media_sensing == "gap" else "^MNM"
    media_type = "^MTT" if cfg.media_type == "thermal_transfer" else "^MTD"

    lines = [
        "^XA",
        "^CI28",  # UTF-8 field data encoding
        f"^PW{layout.canvas_width_px}",
        f"^LL{layout.canvas_height_px}",
        media_tracking,
        media_type,  # ^MTT (thermal transfer, resin/wax ribbon) or ^MTD (direct thermal, no ribbon)
        f"^MD{cfg.darkness}",
        f"^PR{cfg.print_speed}",
    ]

    if logo_gf_field is not None:
        lines += [
            # --- Logo (leftmost) ---
            f"^FO{layout.logo.x},{layout.logo.y}",
            logo_gf_field,
            "^FS",
        ]

    lines += [
        # --- Human-readable text (middle), centered between logo and QR ---
        f"^FO{layout.text.x},{layout.text_render_y}",
        f"^A0N,{layout.text_font_height},{layout.text_font_width}",
        f"^FB{layout.text.w},1,0,C,0",  # center-justify within the available width
        zpl_field_data(cfg.human_text),
        "^FS",
        # --- QR code (rightmost) ---
        f"^FO{layout.qr.x},{layout.qr.y}",
        f"^BQN,2,{layout.qr_magnification}",
        zpl_field_data(f"M{layout_config.qr_error_correction},{cfg.barcode_value}"),  # M=auto mode, ECC letter must match the layout's error_correction
        "^FS",
        f"^PQ{cfg.copies}",
        "^XZ",
    ]
    return "\n".join(lines) + "\n"
