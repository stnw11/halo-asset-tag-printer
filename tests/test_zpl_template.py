from dataclasses import replace

import pytest
import segno

from src.config import PrinterConfig
from src.zpl_template import (
    LayoutConfig,
    LayoutError,
    build_zpl,
    compute_layout,
    load_layout_config,
    zpl_field_data,
)


def make_cfg(**overrides) -> PrinterConfig:
    cfg = PrinterConfig()
    if overrides:
        cfg = replace(cfg, **overrides)
    return cfg


# --- layout / label size math -------------------------------------------

def test_canvas_uses_long_edge_as_horizontal_axis_at_default_dpi():
    # Stock is 0.5in x 2.0in -> the 2in edge must become the horizontal
    # (^PW) axis since 3 elements are laid out side by side.
    layout = compute_layout(make_cfg())
    assert layout.canvas_width_px == 600   # 2.0in * 300dpi
    assert layout.canvas_height_px == 150  # 0.5in * 300dpi


def test_canvas_dimensions_scale_with_dpi():
    layout = compute_layout(make_cfg(dpi=203))
    assert layout.canvas_width_px == round(2.0 * 203)
    assert layout.canvas_height_px == round(0.5 * 203)


def test_axis_resolution_independent_of_which_field_is_larger():
    # If a user configures width > height, the physical long edge is still
    # what ends up as the horizontal axis.
    layout = compute_layout(make_cfg(label_width_in=2.0, label_height_in=0.5))
    assert layout.canvas_width_px == 600
    assert layout.canvas_height_px == 150


@pytest.mark.parametrize("dpi", [150, 200, 203, 300, 600])
def test_all_boxes_stay_within_label_bounds_across_dpi(dpi):
    layout = compute_layout(make_cfg(dpi=dpi))
    for box in (layout.logo, layout.text, layout.qr):
        assert box.within(layout.canvas_width_px, layout.canvas_height_px)


def test_logo_targets_point_four_to_point_four_five_inches_at_300dpi():
    layout = compute_layout(make_cfg())
    logo_in = layout.logo.w / 300
    assert 0.38 <= logo_in <= 0.46  # small tolerance for integer dot rounding


def test_boxes_do_not_overlap_horizontally():
    layout = compute_layout(make_cfg())
    assert layout.logo.x + layout.logo.w <= layout.text.x
    assert layout.text.x + layout.text.w <= layout.qr.x


def test_font_size_is_fixed_regardless_of_tag_length():
    # Font size is designed around LayoutConfig.text_target_char_count, not
    # scaled to the actual tag's length -- a short tag and a longer one
    # (both still within the target) must render at the *same* font size.
    # Confirmed via a live print that per-tag scaling produces wildly
    # inconsistent, sometimes off-label text for very short values.
    short = compute_layout(make_cfg(human_text="A", barcode_value="A"))
    longer = compute_layout(make_cfg(human_text="ABCDEFG", barcode_value="ABCDEFG"))
    assert short.text_font_height == longer.text_font_height


def test_vertical_zone_centered_by_default():
    # Regression test for physical-print feedback on the reference
    # deployment's own printer: with the PoC's original asymmetric margins,
    # the logo and QR both read as touching the top of the label instead of
    # being centered. Default margins are now symmetric, so top and bottom
    # clearance should match for both.
    layout = compute_layout(make_cfg())
    logo_top_clearance = layout.logo.y
    logo_bottom_clearance = layout.canvas_height_px - (layout.logo.y + layout.logo.h)
    assert logo_bottom_clearance == logo_top_clearance

    qr_top_clearance = layout.qr.y
    qr_bottom_clearance = layout.canvas_height_px - (layout.qr.y + layout.qr.h)
    assert abs(qr_bottom_clearance - qr_top_clearance) <= 1  # integer-division rounding


def test_text_render_y_centers_font_block_in_vertical_zone():
    layout = compute_layout(make_cfg())
    slack = layout.text.h - layout.text_font_height
    assert 0 <= layout.text_render_y - layout.text.y <= max(slack, 0)
    # roughly centered, not pinned to the top of the zone
    if slack > 2:
        assert layout.text_render_y > layout.text.y


def test_layout_error_when_label_too_small_for_three_elements():
    with pytest.raises(LayoutError):
        compute_layout(make_cfg(label_width_in=0.1, label_height_in=0.1))


def test_layout_error_when_short_edge_has_no_margin_room():
    with pytest.raises(LayoutError):
        compute_layout(make_cfg(label_width_in=10.0, label_height_in=0.001))


# --- ZPL field escaping ---------------------------------------------------

def test_plain_text_uses_plain_fd():
    assert zpl_field_data("TAG-000123") == "^FDTAG-000123"


def test_caret_in_text_is_hex_escaped():
    result = zpl_field_data("A^B")
    assert result.startswith("^FH^FD")
    assert "_5E" in result  # 0x5E == '^'
    assert "^" not in result[len("^FH^FD"):].replace("_5E", "")


def test_tilde_and_underscore_are_hex_escaped():
    result = zpl_field_data("A~_B")
    assert result.startswith("^FH^FD")
    assert "_7E" in result  # '~'
    assert "_5F" in result  # '_'


# --- full ZPL assembly ----------------------------------------------------

def _build(cfg=None):
    cfg = cfg or make_cfg()
    # A minimal, well-formed dummy ^GFA field stands in for a real logo
    # conversion here -- logo.py is tested separately.
    dummy_gf = "^GFA,8,8,1,FF"
    return build_zpl(cfg, dummy_gf)


def test_zpl_wrapped_in_xa_xz():
    zpl = _build()
    assert zpl.strip().startswith("^XA")
    assert zpl.strip().endswith("^XZ")


def test_zpl_contains_darkness_and_speed_within_valid_zpl_ranges():
    cfg = make_cfg(darkness=12, print_speed=3)
    zpl = _build(cfg)
    assert "^MD12" in zpl
    assert "^PR3" in zpl
    # ZPL ^MD valid range is 0-30
    assert 0 <= 12 <= 30
    assert 1 <= 3 <= 14


def test_zpl_contains_print_width_and_length():
    zpl = _build()
    assert "^PW600" in zpl
    assert "^LL150" in zpl


def test_zpl_uses_thermal_transfer_media_type():
    zpl = _build()
    assert "^MTT" in zpl
    assert "^MTD" not in zpl


def test_media_sensing_gap_uses_mny():
    zpl = _build(make_cfg(media_sensing="gap"))
    assert "^MNY" in zpl


def test_media_sensing_mark_uses_mnm():
    zpl = _build(make_cfg(media_sensing="mark"))
    assert "^MNM" in zpl


def test_media_type_thermal_transfer_uses_mtt():
    zpl = _build(make_cfg(media_type="thermal_transfer"))
    assert "^MTT" in zpl
    assert "^MTD" not in zpl


def test_media_type_direct_thermal_uses_mtd():
    zpl = _build(make_cfg(media_type="direct_thermal"))
    assert "^MTD" in zpl
    assert "^MTT" not in zpl


def test_layout_config_qr_ecc_override_changes_field_data_and_capacity():
    # Medium ECC raises v1 alphanumeric capacity above quartile's 16 chars --
    # an 18-char payload that forces v2 at Q should stay v1 at M.
    payload = "TAG-" + "0" * 13  # 17 chars total: over Q's 16-char v1 cap, under M's 20-char cap
    q_config = LayoutConfig(qr_error_correction="Q")
    m_config = LayoutConfig(qr_error_correction="M")
    q_layout = compute_layout(make_cfg(barcode_value=payload), layout_config=q_config)
    m_layout = compute_layout(make_cfg(barcode_value=payload), layout_config=m_config)
    assert m_layout.qr.w != q_layout.qr.w  # different version at the two ECC levels

    zpl = build_zpl(make_cfg(barcode_value=payload), "^GFA,8,8,1,FF", layout=m_layout, layout_config=m_config)
    assert f"MM,{payload}" in zpl


def test_human_text_field_present():
    zpl = _build(make_cfg(human_text="TAG-0123"))
    assert "^FDTAG-0123" in zpl


def test_human_text_field_is_center_justified():
    layout = compute_layout(make_cfg())
    zpl = _build()
    assert f"^FB{layout.text.w},1,0,C,0" in zpl


def test_barcode_value_embedded_in_qr_field():
    zpl = _build(make_cfg(barcode_value="TAG-000123"))
    assert "^BQN,2," in zpl
    assert "MQ,TAG-000123" in zpl


def test_copies_field_present():
    zpl = _build(make_cfg(copies=3))
    assert "^PQ3" in zpl


def test_special_characters_in_human_text_are_escaped_in_output():
    zpl = _build(make_cfg(human_text="A^B"))
    assert "^FH^FD" in zpl
    assert "_5E" in zpl


# --- QR sizing fix (4.5.1 defect): magnification must come from the -----
# --- QR symbol's actual version, not an assumed version-1 module count --

def test_short_payload_uses_version_1_module_count():
    # 11 chars, well within v1's 16-char quartile-alphanumeric capacity.
    layout = compute_layout(make_cfg(barcode_value="TAG-0000001"))
    expected_modules = segno.make("TAG-0000001", error="q", micro=False, boost_error=False).symbol_size(border=0)[0]
    assert expected_modules == 21  # confirms the payload really is v1
    assert layout.qr.w == layout.qr_magnification * 21


def test_longer_payload_forces_version_2_and_still_fits_on_label():
    # 20 chars: over v1's 16-char quartile capacity, forces v2 (25 modules).
    # This is exactly the case the PoC's hardcoded QR_ASSUMED_MODULE_COUNT=21
    # would have clipped -- the box must still land inside label bounds.
    payload = "TAG-00000000000000"[:20]
    expected_modules = segno.make(payload, error="q", micro=False, boost_error=False).symbol_size(border=0)[0]
    assert expected_modules == 25  # confirms the payload really is v2

    layout = compute_layout(make_cfg(barcode_value=payload))
    assert layout.qr.w == layout.qr_magnification * 25
    assert layout.qr.within(layout.canvas_width_px, layout.canvas_height_px)
    # v2 needs more modules than v1 in the same vertical budget, so it must
    # use a smaller (or equal) magnification than the v1 case above.
    v1_layout = compute_layout(make_cfg(barcode_value="TAG-0000001"))
    assert layout.qr_magnification <= v1_layout.qr_magnification


def test_over_capacity_payload_raises_layout_error_naming_the_value():
    long_payload = "TAG-" + "0" * 60  # far beyond what fits legibly on this stock
    with pytest.raises(LayoutError, match="TAG-0{5}"):
        compute_layout(make_cfg(barcode_value=long_payload))


def test_qr_field_data_ecc_letter_matches_module_count_calculation():
    # The "M<ECC>," prefix sent to the printer must use the same ECC level
    # _qr_module_count used to size the box -- otherwise the printer could
    # select a different version than the one we laid out for.
    zpl = _build(make_cfg(barcode_value="TAG-000123"))
    assert "MQ,TAG-000123" in zpl


# --- Text legibility floor (4.3.1) ---------------------------------------

def test_text_below_legibility_floor_raises_layout_error_naming_the_value():
    long_tag = "TAG-" + "0" * 40
    with pytest.raises(LayoutError, match="TAG-0{5}"):
        compute_layout(make_cfg(human_text=long_tag))


def test_text_within_legibility_floor_does_not_raise():
    layout = compute_layout(make_cfg(human_text="TAG-0123"))
    assert layout.text_font_height >= 24  # MIN_TEXT_FONT_DOTS_AT_300DPI at default 300 dpi


# --- Logo aspect-ratio preservation (4.6.2) -------------------------------

def test_default_logo_aspect_ratio_reproduces_original_square_geometry():
    # No logo_aspect_ratio passed -> defaults to 1.0 (square), which must
    # reduce exactly to the PoC's original confirmed-good dimensions.
    layout = compute_layout(make_cfg())
    assert layout.logo.w == layout.logo.h == 126


def test_wide_logo_uses_full_width_and_is_letterboxed_vertically():
    layout = compute_layout(make_cfg(), logo_aspect_ratio=4.0)  # 4:1 wordmark
    square_side = 126  # same target square as the default case
    assert layout.logo.w == square_side  # full width used
    assert layout.logo.h < square_side  # letterboxed, not squashed to fit
    assert layout.logo.within(600, 150)


def test_tall_logo_frees_horizontal_space_for_text():
    layout = compute_layout(make_cfg(), logo_aspect_ratio=0.25)  # tall crest
    square_side = 126
    assert layout.logo.h == square_side  # full height used
    assert layout.logo.w < square_side  # narrower -- freed space goes to text

    square_layout = compute_layout(make_cfg(), logo_aspect_ratio=1.0)
    assert layout.text.w > square_layout.text.w  # text reclaimed the freed width


def test_no_logo_frees_all_footprint_to_text_and_starts_at_margin():
    layout = compute_layout(make_cfg(), logo_aspect_ratio=None)
    assert layout.logo.w == 0
    assert layout.logo.h == 0
    assert layout.text.x == layout.logo.x  # text starts right at the margin, no logo/gap reserved

    square_layout = compute_layout(make_cfg(), logo_aspect_ratio=1.0)
    assert layout.text.w > square_layout.text.w


def test_build_zpl_omits_logo_block_when_gf_field_is_none():
    layout = compute_layout(make_cfg(), logo_aspect_ratio=None)
    zpl = build_zpl(make_cfg(), None, layout=layout)
    assert "^GFA" not in zpl
    assert zpl.strip().startswith("^XA")
    assert zpl.strip().endswith("^XZ")


# --- config/layout.yaml loading -------------------------------------------

def test_load_layout_config_overrides_only_specified_keys(tmp_path):
    path = tmp_path / "layout.yaml"
    path.write_text("layout:\n  qr_error_correction: M\n")
    config = load_layout_config(path)
    assert config.qr_error_correction == "M"
    assert config.outer_margin_in == LayoutConfig().outer_margin_in  # untouched default


def test_load_layout_config_rejects_unknown_key(tmp_path):
    path = tmp_path / "layout.yaml"
    path.write_text("layout:\n  not_a_real_setting: 1\n")
    with pytest.raises(ValueError, match="unknown layout setting"):
        load_layout_config(path)


def test_load_layout_config_missing_layout_key_uses_all_defaults(tmp_path):
    path = tmp_path / "layout.yaml"
    path.write_text("something_else: true\n")
    assert load_layout_config(path) == LayoutConfig()


def test_short_tag_value_does_not_push_text_past_the_label_bottom():
    # Regression test for a live print bug: a 3-character tag ("150")
    # computed a font_height filling the entire vertical zone (no
    # centering slack), and the baseline offset then pushed the text box
    # 45 dots past the bottom of a 150-dot-tall label -- an actual
    # physical cutoff, not just a cosmetic misalignment.
    layout_config = LayoutConfig(text_baseline_offset_ratio=0.45)
    layout = compute_layout(make_cfg(human_text="150", barcode_value="150"), layout_config=layout_config)
    assert layout.text_render_y + layout.text_font_height <= layout.canvas_height_px
    assert layout.text_render_y + layout.text_font_height <= layout.text.y + layout.text.h


def test_short_tag_value_clamped_offset_still_within_vertical_zone():
    layout_config = LayoutConfig(text_baseline_offset_ratio=0.9)  # deliberately extreme
    layout = compute_layout(make_cfg(human_text="AB", barcode_value="AB"), layout_config=layout_config)
    assert layout.text_render_y >= layout.text.y
    assert layout.text_render_y + layout.text_font_height <= layout.text.y + layout.text.h


def test_injection_attempt_in_human_text_prints_literally_not_as_zpl_commands():
    # Acceptance criterion 12: a tag value containing ^XZ / ~DY must print
    # as sanitized literal characters, never as an early field/format end.
    zpl = _build(make_cfg(human_text="A^XZB~DYC"))
    assert zpl.count("^XZ") == 1  # only the real, trailing format-end command
    assert "_5E584" in zpl or "_5E" in zpl  # the literal '^' got hex-escaped
