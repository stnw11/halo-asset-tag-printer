"""Bring-up and troubleshooting CLI: print a tag with no Halo involved.

Ported from the brady-i4311-printer PoC's cli.py. Build config, render the
label, and either send it to the printer over a raw socket or write it to
a local file (--dry-run).

Run from the repo root:
    python -m tools.print_test_tag --dry-run
    python -m tools.print_test_tag --printer-ip 192.168.1.50
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from src.config import DEFAULT_CONFIG_DIR, PrinterConfig, build_config
from src.logo import convert_image_to_gf_field, image_aspect_ratio, resolve_logo
from src.printer_client import PrinterConnectionError, dry_run_write, send_zpl
from src.zpl_template import DEFAULT_LAYOUT_CONFIG, Layout, LayoutConfig, build_zpl, compute_layout, load_layout_config


def load_layout_config_or_default(config_dir: Path = DEFAULT_CONFIG_DIR) -> LayoutConfig:
    layout_path = config_dir / "layout.yaml"
    return load_layout_config(layout_path) if layout_path.exists() else DEFAULT_LAYOUT_CONFIG


def render_label(cfg: PrinterConfig, layout_config: LayoutConfig = DEFAULT_LAYOUT_CONFIG) -> tuple[str, Layout]:
    # resolve_logo() never raises -- an empty LOGO_ASSET or a load failure
    # both mean "print without a logo", not a fatal error (see 4.6.1).
    image = resolve_logo(cfg.logo_asset)
    aspect_ratio = image_aspect_ratio(image) if image is not None else None
    layout = compute_layout(
        cfg, logo_aspect_ratio=aspect_ratio, layout_config=layout_config
    )
    logo_gf_field = (
        convert_image_to_gf_field(image, size_px=(layout.logo.w, layout.logo.h), method=cfg.logo_method)
        if image is not None
        else None
    )
    zpl = build_zpl(cfg, logo_gf_field, layout=layout, layout_config=layout_config)
    return zpl, layout


def main(argv: list[str] | None = None) -> int:
    load_dotenv()  # fills in os.environ from .env for vars not already set
    try:
        cfg = build_config(argv)
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    try:
        layout_config = load_layout_config_or_default()
        zpl, layout = render_label(cfg, layout_config)
    except Exception as exc:  # noqa: BLE001 - surface any render failure clearly to the operator
        print(f"Failed to build label: {exc}", file=sys.stderr)
        return 3

    print(
        f"Label: {cfg.label_width_in}in x {cfg.label_height_in}in stock @ {cfg.dpi} DPI "
        f"-> ^PW{layout.canvas_width_px} x ^LL{layout.canvas_height_px} dots (long edge horizontal)"
    )
    print(f"  logo box: {layout.logo}")
    print(
        f"  text box: {layout.text} (font {layout.text_font_height}x{layout.text_font_width}, "
        f"render y={layout.text_render_y}, centered)"
    )
    print(f"  qr box:   {layout.qr} (magnification {layout.qr_magnification})")

    if cfg.dry_run:
        out_path = dry_run_write(zpl, cfg.dry_run_path)
        print(f"DRY_RUN: wrote {len(zpl)} chars of ZPL to {out_path}")
        return 0

    try:
        sent = send_zpl(zpl, cfg.printer_ip, cfg.printer_port, timeout=cfg.connect_timeout)
    except PrinterConnectionError as exc:
        print(f"Print failed: {exc}", file=sys.stderr)
        return 1

    print(f"Sent {sent} bytes to {cfg.printer_ip}:{cfg.printer_port} ({cfg.copies} cop{'y' if cfg.copies == 1 else 'ies'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
