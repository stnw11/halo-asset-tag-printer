"""Configuration for the print-side pipeline (printer, layout, logo).

PrinterConfig is assembled in three layers, each overriding the last:
  1. config/printers.yaml (a named printer's hardware settings), if present
  2. environment variables (.env)
  3. CLI flags

Layer 1 is optional by design: tools/print_test_tag.py is a bring-up and
troubleshooting tool (see the project's technical spec, section 4.1), and
should work with zero setup for quick iteration -- the neutral built-in
defaults below stand in when config/printers.yaml doesn't exist yet. Once
it does exist, its printer's `ip` is checked against the unedited example
address and rejected with a clear message (fail-fast, see StartupError)
-- a wrong default IP is worse than a missing one.

Every value here is either a documented-example placeholder (no real
printer/site value belongs in a committed file) or a reasonable default
that still needs verification/tuning against the real printer in use.
"""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_DIR = REPO_ROOT / "config"


class StartupError(ValueError):
    """Bad config found at startup -- malformed YAML, an unedited example
    IP, or a named printer that doesn't exist. Subclasses ValueError so
    existing `except ValueError` callers (tools/print_test_tag.py) keep
    working unchanged; callers that want to distinguish "fail fast, don't
    retry" from other validation errors can catch this specifically."""

# --- Neutral, committed defaults -- replace via env/CLI/config for your site
DEFAULT_PRINTER_IP = "192.0.2.10"              # RFC 5737 documentation address -- replace, do not commit a real IP
DEFAULT_PRINTER_PORT = 9100                    # Brady/Zebra raw-IP / JetDirect-style port
DEFAULT_LABEL_WIDTH_IN = 0.5                   # Brady B33-53-423-class stock; override for your die-cut size
DEFAULT_LABEL_HEIGHT_IN = 2.0

# --- Reasonable defaults that need verification against the physical unit -
DEFAULT_DPI = 300                              # UNVERIFIED: common Brady i-series resolution, confirm via config/status label
DEFAULT_MEDIA_SENSING = "gap"                  # UNVERIFIED: confirm gap vs black-mark against your stock
DEFAULT_DARKNESS = 15                          # UNVERIFIED baseline (ZPL ^MD range 0-30); start conservative for resin ribbon
DEFAULT_PRINT_SPEED = 2                        # UNVERIFIED baseline, inches/sec; resin ribbons want slower speed than wax
DEFAULT_COPIES = 1
DEFAULT_BARCODE_VALUE = "TAG-0123"  # 8 chars -- fits within the default text_target_char_count=9 (see zpl_template.LayoutConfig)
DEFAULT_HUMAN_TEXT = "TAG-0123"
DEFAULT_LOGO_ASSET = "assets/placeholder_logo.png"
DEFAULT_LOGO_METHOD = "threshold"              # see logo.py -- verified by rendering both methods side by side

DEFAULT_MEDIA_TYPE = "thermal_transfer"        # thermal_transfer (^MTT, ribbon) or direct_thermal (^MTD)
DEFAULT_RETRIES = 3

VALID_MEDIA_SENSING = ("gap", "mark")
VALID_MEDIA_TYPES = ("thermal_transfer", "direct_thermal")
VALID_LOGO_METHODS = ("dither", "threshold")
MIN_DARKNESS, MAX_DARKNESS = 0, 30
MIN_SPEED, MAX_SPEED = 1, 14


@dataclass(frozen=True)
class PrinterConfig:
    printer_ip: str = DEFAULT_PRINTER_IP
    printer_port: int = DEFAULT_PRINTER_PORT
    dpi: int = DEFAULT_DPI
    label_width_in: float = DEFAULT_LABEL_WIDTH_IN
    label_height_in: float = DEFAULT_LABEL_HEIGHT_IN
    darkness: int = DEFAULT_DARKNESS
    print_speed: int = DEFAULT_PRINT_SPEED
    media_sensing: str = DEFAULT_MEDIA_SENSING
    media_type: str = DEFAULT_MEDIA_TYPE
    retries: int = DEFAULT_RETRIES
    barcode_value: str = DEFAULT_BARCODE_VALUE
    human_text: str = DEFAULT_HUMAN_TEXT
    copies: int = DEFAULT_COPIES
    dry_run: bool = False
    dry_run_path: str = "output.zpl"
    logo_asset: str = DEFAULT_LOGO_ASSET
    logo_method: str = DEFAULT_LOGO_METHOD
    connect_timeout: float = 5.0

    def validate(self) -> None:
        errors = []
        if self.dpi <= 0:
            errors.append(f"DPI must be positive, got {self.dpi}")
        if self.label_width_in <= 0 or self.label_height_in <= 0:
            errors.append("Label width/height must be positive inches")
        if not (MIN_DARKNESS <= self.darkness <= MAX_DARKNESS):
            errors.append(
                f"DARKNESS must be between {MIN_DARKNESS} and {MAX_DARKNESS} (ZPL ^MD range), got {self.darkness}"
            )
        if not (MIN_SPEED <= self.print_speed <= MAX_SPEED):
            errors.append(
                f"PRINT_SPEED must be between {MIN_SPEED} and {MAX_SPEED} in/sec, got {self.print_speed}"
            )
        if self.media_sensing not in VALID_MEDIA_SENSING:
            errors.append(
                f"MEDIA_SENSING must be one of {VALID_MEDIA_SENSING}, got {self.media_sensing!r}"
            )
        if self.media_type not in VALID_MEDIA_TYPES:
            errors.append(
                f"MEDIA_TYPE must be one of {VALID_MEDIA_TYPES}, got {self.media_type!r}"
            )
        if self.retries < 0:
            errors.append(f"RETRIES must be >= 0, got {self.retries}")
        if self.logo_method not in VALID_LOGO_METHODS:
            errors.append(
                f"LOGO_METHOD must be one of {VALID_LOGO_METHODS}, got {self.logo_method!r}"
            )
        if self.copies < 1:
            errors.append(f"COPIES must be >= 1, got {self.copies}")
        if not self.barcode_value:
            errors.append("BARCODE_VALUE must not be empty")
        if not self.human_text:
            errors.append("HUMAN_TEXT must not be empty")
        if not (1 <= self.printer_port <= 65535):
            errors.append(f"PRINTER_PORT must be a valid TCP port, got {self.printer_port}")
        if errors:
            raise ValueError("Invalid configuration:\n  - " + "\n  - ".join(errors))


def env_bool(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _config_from_env() -> PrinterConfig:
    env = os.environ
    return PrinterConfig(
        printer_ip=env.get("PRINTER_IP", DEFAULT_PRINTER_IP),
        printer_port=int(env.get("PRINTER_PORT", DEFAULT_PRINTER_PORT)),
        dpi=int(env.get("DPI", DEFAULT_DPI)),
        label_width_in=float(env.get("LABEL_WIDTH_IN", DEFAULT_LABEL_WIDTH_IN)),
        label_height_in=float(env.get("LABEL_HEIGHT_IN", DEFAULT_LABEL_HEIGHT_IN)),
        darkness=int(env.get("DARKNESS", DEFAULT_DARKNESS)),
        print_speed=int(env.get("PRINT_SPEED", DEFAULT_PRINT_SPEED)),
        media_sensing=env.get("MEDIA_SENSING", DEFAULT_MEDIA_SENSING).strip().lower(),
        media_type=env.get("MEDIA_TYPE", DEFAULT_MEDIA_TYPE).strip().lower(),
        retries=int(env.get("RETRIES", DEFAULT_RETRIES)),
        barcode_value=env.get("BARCODE_VALUE", DEFAULT_BARCODE_VALUE),
        human_text=env.get("HUMAN_TEXT", DEFAULT_HUMAN_TEXT),
        copies=int(env.get("COPIES", DEFAULT_COPIES)),
        dry_run=env_bool("DRY_RUN", False),
        dry_run_path=env.get("DRY_RUN_PATH", "output.zpl"),
        logo_asset=env.get("LOGO_ASSET", DEFAULT_LOGO_ASSET),
        logo_method=env.get("LOGO_METHOD", DEFAULT_LOGO_METHOD).strip().lower(),
        connect_timeout=float(env.get("CONNECT_TIMEOUT", 5.0)),
    )


_ENV_VAR_BY_FIELD = {
    "printer_ip": "PRINTER_IP",
    "printer_port": "PRINTER_PORT",
    "dpi": "DPI",
    "label_width_in": "LABEL_WIDTH_IN",
    "label_height_in": "LABEL_HEIGHT_IN",
    "darkness": "DARKNESS",
    "print_speed": "PRINT_SPEED",
    "media_sensing": "MEDIA_SENSING",
    "media_type": "MEDIA_TYPE",
    "retries": "RETRIES",
    "barcode_value": "BARCODE_VALUE",
    "human_text": "HUMAN_TEXT",
    "copies": "COPIES",
    "dry_run": "DRY_RUN",
    "dry_run_path": "DRY_RUN_PATH",
    "logo_asset": "LOGO_ASSET",
    "logo_method": "LOGO_METHOD",
    "connect_timeout": "CONNECT_TIMEOUT",
}


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Send a ZPL test label to a ZPL-compatible printer over a raw TCP socket."
    )
    parser.add_argument("--printer-ip", dest="printer_ip")
    parser.add_argument("--printer-port", dest="printer_port", type=int)
    parser.add_argument("--dpi", dest="dpi", type=int)
    parser.add_argument("--label-width-in", dest="label_width_in", type=float)
    parser.add_argument("--label-height-in", dest="label_height_in", type=float)
    parser.add_argument("--darkness", dest="darkness", type=int)
    parser.add_argument("--print-speed", dest="print_speed", type=int)
    parser.add_argument("--media-sensing", dest="media_sensing", choices=VALID_MEDIA_SENSING)
    parser.add_argument("--media-type", dest="media_type", choices=VALID_MEDIA_TYPES)
    parser.add_argument("--retries", dest="retries", type=int)
    parser.add_argument("--barcode-value", dest="barcode_value")
    parser.add_argument("--human-text", dest="human_text")
    parser.add_argument("--copies", dest="copies", type=int)
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", default=None)
    parser.add_argument("--dry-run-path", dest="dry_run_path")
    parser.add_argument("--logo-asset", dest="logo_asset")
    parser.add_argument("--logo-method", dest="logo_method", choices=VALID_LOGO_METHODS)
    parser.add_argument("--connect-timeout", dest="connect_timeout", type=float)
    return parser


def _load_printer_hardware_kwargs(printers_path: Path, printer_name: str) -> dict:
    try:
        with open(printers_path) as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        raise StartupError(f"malformed YAML in {printers_path}: {exc}") from exc

    printers = data.get("printers") or {}
    if printer_name not in printers:
        raise StartupError(
            f"printer {printer_name!r} not found in {printers_path} "
            f"(available: {sorted(printers) or 'none'})"
        )
    raw = printers[printer_name]

    if raw.get("ip") == DEFAULT_PRINTER_IP:
        raise StartupError(
            f"{printers_path} still has the example printer IP ({DEFAULT_PRINTER_IP}) "
            "-- edit it to your printer's real LAN address before starting"
        )

    return dict(
        printer_ip=raw["ip"],
        printer_port=raw.get("port", DEFAULT_PRINTER_PORT),
        dpi=raw.get("dpi", DEFAULT_DPI),
        label_width_in=raw.get("label_width_in", DEFAULT_LABEL_WIDTH_IN),
        label_height_in=raw.get("label_height_in", DEFAULT_LABEL_HEIGHT_IN),
        media_sensing=str(raw.get("media_sensing", DEFAULT_MEDIA_SENSING)).strip().lower(),
        media_type=str(raw.get("media_type", DEFAULT_MEDIA_TYPE)).strip().lower(),
        darkness=raw.get("darkness", DEFAULT_DARKNESS),
        print_speed=raw.get("print_speed", DEFAULT_PRINT_SPEED),
        retries=raw.get("retries", DEFAULT_RETRIES),
        connect_timeout=float(raw.get("connect_timeout_seconds", 5.0)),
    )


def build_config(argv: list[str] | None = None, config_dir: Path | None = None) -> PrinterConfig:
    """Build config in three layers: config/printers.yaml (if present) as
    the base, environment variables next, then CLI flags on top."""
    config_dir = config_dir or Path(os.environ.get("CONFIG_DIR", DEFAULT_CONFIG_DIR))
    printers_path = config_dir / "printers.yaml"

    if printers_path.exists():
        printer_name = os.environ.get("PRINTER_NAME", "default")
        cfg = PrinterConfig(**_load_printer_hardware_kwargs(printers_path, printer_name))
    else:
        cfg = PrinterConfig()  # no config/printers.yaml yet -- neutral built-in defaults

    env_cfg = _config_from_env()
    # Only override with an env var's value where the corresponding env var
    # was actually set -- otherwise _config_from_env()'s own defaults would
    # clobber whatever printers.yaml just supplied.
    env_overrides = {
        field: getattr(env_cfg, field)
        for field, env_name in _ENV_VAR_BY_FIELD.items()
        if os.environ.get(env_name) is not None
    }
    if env_overrides:
        cfg = replace(cfg, **env_overrides)

    args = _build_arg_parser().parse_args(argv)
    cli_overrides = {k: v for k, v in vars(args).items() if v is not None}
    if cli_overrides:
        cfg = replace(cfg, **cli_overrides)

    cfg.validate()
    return cfg
