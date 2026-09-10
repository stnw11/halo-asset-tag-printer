import os
import time
from pathlib import Path

import pytest
from PIL import Image

from src.logo import (
    LogoCache,
    bitmap_to_gf_field,
    convert_logo_to_gf_field,
    image_aspect_ratio,
    load_logo,
    render_ink_bitmap,
    resolve_logo,
)

PLACEHOLDER_ASSET_PATH = Path(__file__).resolve().parents[1] / "assets" / "placeholder_logo.png"


def _synthetic_logo() -> Image.Image:
    """A small stand-in logo: an orange square inset in a gray-ringed white
    square, used for unit tests that shouldn't depend on the real shipped
    placeholder asset for every case."""
    img = Image.new("RGBA", (100, 100), (255, 255, 255, 255))
    for x in range(100):
        for y in range(100):
            if 40 <= x < 60 and 40 <= y < 60:
                img.putpixel((x, y), (245, 166, 35, 255))  # orange, saturated
            elif x < 3 or x > 96 or y < 3 or y > 96:
                img.putpixel((x, y), (88, 89, 91, 255))  # neutral gray ring
    return img


@pytest.mark.parametrize("method", ["dither", "threshold"])
def test_render_ink_bitmap_produces_expected_dimensions(method):
    bitmap = render_ink_bitmap(_synthetic_logo(), size_px=(132, 132), method=method)
    assert bitmap.width == 132
    assert bitmap.height == 132
    assert bitmap.bytes_per_row == (132 + 7) // 8
    assert len(bitmap.rows) == 132


def test_render_ink_bitmap_is_non_empty_bitmap():
    bitmap = render_ink_bitmap(_synthetic_logo(), size_px=(60, 60), method="dither")
    total_ink_bits = sum(bin(byte).count("1") for row in bitmap.rows for byte in row)
    assert total_ink_bits > 0


def test_saturated_color_survives_ink_conversion_better_than_plain_luminance():
    """Regression check for the known failure mode: plain grayscale
    luminance scores this logo's orange square (~L 163) as LIGHTER than its
    gray ring (~L 89), so a mid threshold can drop the orange region while
    keeping the ring. Our ink-intensity metric (distance-from-white on the
    most-saturated channel) must score the orange region as ink too."""
    img = _synthetic_logo()
    bitmap = render_ink_bitmap(img, size_px=(100, 100), method="threshold", threshold=96)
    # Orange square occupies pixels [40,60)x[40,60) in source coordinates,
    # which maps 1:1 here since size_px == source size.
    orange_ink_bits = 0
    for y in range(40, 60):
        row = bitmap.rows[y]
        for x in range(40, 60):
            if row[x // 8] & (0x80 >> (x % 8)):
                orange_ink_bits += 1
    assert orange_ink_bits > 0, "orange region was dropped entirely by ink thresholding"


def test_bitmap_to_gf_field_format():
    bitmap = render_ink_bitmap(_synthetic_logo(), size_px=(16, 8), method="threshold")
    field = bitmap_to_gf_field(bitmap)
    assert field.startswith("^GFA,")
    parts = field.split(",", 4)
    assert len(parts) == 5
    _, total_bytes, total_bytes2, bytes_per_row, hex_data = parts
    assert total_bytes == total_bytes2
    assert int(bytes_per_row) == 2  # ceil(16/8)
    assert int(total_bytes) == 2 * 8  # bytes_per_row * height
    assert len(hex_data) == int(total_bytes) * 2  # 2 hex chars per byte
    int(hex_data, 16)  # must be valid hex


def test_unknown_method_raises():
    with pytest.raises(ValueError):
        render_ink_bitmap(_synthetic_logo(), size_px=(10, 10), method="bogus")


def test_invalid_size_raises():
    with pytest.raises(ValueError):
        render_ink_bitmap(_synthetic_logo(), size_px=(0, 10), method="dither")


def test_placeholder_asset_converts_at_target_size():
    # The committed placeholder ships with the repo -- unlike the PoC's
    # real logo, this one is always present, so no skipif needed.
    field = convert_logo_to_gf_field(str(PLACEHOLDER_ASSET_PATH), size_px=(132, 132), method="threshold")
    assert field.startswith("^GFA,")
    _, total_bytes, _, bytes_per_row, hex_data = field.split(",", 4)
    assert int(bytes_per_row) == (132 + 7) // 8
    assert int(total_bytes) == int(bytes_per_row) * 132
    assert len(hex_data) == int(total_bytes) * 2


def test_placeholder_asset_produces_non_empty_ink():
    bitmap = render_ink_bitmap(load_logo(str(PLACEHOLDER_ASSET_PATH)), size_px=(132, 132), method="threshold")
    total_ink_bits = sum(bin(b).count("1") for row in bitmap.rows for b in row)
    assert total_ink_bits > 0


# --- resolve_logo() / image_aspect_ratio() (4.6.1) ------------------------

def test_resolve_logo_empty_path_returns_none():
    assert resolve_logo("") is None
    assert resolve_logo(None) is None


def test_resolve_logo_missing_file_returns_none_and_warns(caplog):
    with caplog.at_level("WARNING"):
        result = resolve_logo("/nonexistent/path/does-not-exist.png")
    assert result is None
    assert "could not load logo" in caplog.text


def test_resolve_logo_loads_a_real_file():
    image = resolve_logo(str(PLACEHOLDER_ASSET_PATH))
    assert image is not None


def test_image_aspect_ratio_wide_and_tall():
    wide = Image.new("RGB", (400, 100))
    tall = Image.new("RGB", (100, 400))
    square = Image.new("RGB", (100, 100))
    assert image_aspect_ratio(wide) == 4.0
    assert image_aspect_ratio(tall) == 0.25
    assert image_aspect_ratio(square) == 1.0


# --- LogoCache (4.6.2 caching + periodic reload) --------------------------

def test_logo_cache_converts_once_and_reuses_cached_field(tmp_path):
    logo_path = tmp_path / "logo.png"
    _synthetic_logo().save(logo_path)
    cache = LogoCache(str(logo_path), refresh_seconds=300)

    field_a = cache.get_gf_field((64, 64), now=1000.0)
    field_b = cache.get_gf_field((64, 64), now=1000.5)  # well within refresh window
    assert field_a == field_b
    assert field_a.startswith("^GFA,")


def test_logo_cache_reloads_after_refresh_window_if_file_changed(tmp_path):
    logo_path = tmp_path / "logo.png"
    _synthetic_logo().save(logo_path)
    cache = LogoCache(str(logo_path), refresh_seconds=1.0)

    first = cache.get_gf_field((64, 64), now=1000.0)

    # Simulate an adopter swapping the logo file: different image, and an
    # mtime bump so the cache's change-detection actually fires.
    replacement = Image.new("RGBA", (100, 100), (255, 255, 255, 255))
    for x in range(100):
        for y in range(100):
            if 10 <= x < 90 and 10 <= y < 90:
                replacement.putpixel((x, y), (0, 0, 0, 255))
    replacement.save(logo_path)
    bumped_mtime = time.time() + 5
    os.utime(logo_path, (bumped_mtime, bumped_mtime))

    second = cache.get_gf_field((64, 64), now=1002.0)  # past the 1s refresh window
    assert second != first


def test_logo_cache_does_not_reload_within_refresh_window_even_if_file_changes(tmp_path):
    logo_path = tmp_path / "logo.png"
    _synthetic_logo().save(logo_path)
    cache = LogoCache(str(logo_path), refresh_seconds=300)

    first = cache.get_gf_field((64, 64), now=1000.0)

    replacement = Image.new("RGBA", (100, 100), (0, 0, 0, 255))
    replacement.save(logo_path)

    still_cached = cache.get_gf_field((64, 64), now=1001.0)  # within the 300s window
    assert still_cached == first


def test_logo_cache_missing_file_returns_none_without_raising(tmp_path):
    cache = LogoCache(str(tmp_path / "missing.png"))
    assert cache.get_gf_field((64, 64), now=1000.0) is None


def test_logo_cache_empty_path_returns_none():
    cache = LogoCache("")
    assert cache.get_gf_field((64, 64), now=1000.0) is None


def test_logo_cache_aspect_ratio_reflects_loaded_image(tmp_path):
    logo_path = tmp_path / "wide.png"
    Image.new("RGBA", (400, 100), (0, 0, 0, 255)).save(logo_path)
    cache = LogoCache(str(logo_path))
    cache.get_gf_field((64, 16), now=1000.0)
    assert cache.aspect_ratio == 4.0


def test_logo_cache_aspect_ratio_is_none_before_anything_loads():
    # Regression: a fresh cache must not silently report "no logo" just
    # because get_gf_field() (the only other thing that used to trigger a
    # load) hasn't been called yet -- callers need aspect_ratio before
    # they can compute a layout, which is before they know what size to
    # ask get_gf_field() for.
    cache = LogoCache("/nonexistent/never-checked.png")
    assert cache.aspect_ratio is None


def test_logo_cache_ensure_loaded_makes_aspect_ratio_available(tmp_path):
    logo_path = tmp_path / "wide.png"
    Image.new("RGBA", (400, 100), (0, 0, 0, 255)).save(logo_path)
    cache = LogoCache(str(logo_path))
    assert cache.aspect_ratio is None  # nothing loaded yet
    cache.ensure_loaded(now=1000.0)
    assert cache.aspect_ratio == 4.0
