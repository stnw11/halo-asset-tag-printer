"""Poll loop: the only place claim-before-print ordering is enforced (see
the project spec, section 4.4). Also touches /tmp/heartbeat each
iteration for the Docker healthcheck, and honors SHADOW_MODE.

Startup fails fast on bad config (malformed YAML, an unedited example
printer IP, missing Halo credentials) -- a misconfigured print service
should not start. Once running, per-asset and per-poll failures are
logged and skipped, never fatal: a Halo outage, a printer outage, or one
asset with a bad tag value must never crash the container or stop the
rest of the batch (see 4.4's rules).
"""
from __future__ import annotations

import logging
import os
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path

from dotenv import load_dotenv

from .asset_source import get_pending
from .config import DEFAULT_CONFIG_DIR, StartupError, build_config, env_bool
from .fields import load_fields_config
from .guards import apply_batch_cap, clamp_qty
from .halo_client import HaloClient
from .logo import LogoCache
from .printer_client import PrinterConnectionError, send_batch
from .zpl_template import (
    DEFAULT_LAYOUT_CONFIG,
    LayoutConfig,
    LayoutError,
    build_zpl,
    compute_layout,
    load_layout_config,
)

logger = logging.getLogger(__name__)

HEARTBEAT_PATH = Path("/tmp/heartbeat")

REQUIRED_HALO_ENV_VARS = (
    "HALO_BASE_URL",
    "HALO_AUTH_URL",
    "HALO_CLIENT_ID",
    "HALO_CLIENT_SECRET",
    "HALO_ASSET_TAG_QTY_FIELD_ID",
)


@dataclass(frozen=True)
class OperationalConfig:
    halo_base_url: str
    halo_auth_url: str
    halo_client_id: str
    halo_client_secret: str
    qty_field_id: int
    poll_interval_seconds: float = 15.0
    max_tags_per_asset: int = 5
    max_assets_per_poll: int = 25
    shadow_mode: bool = False
    logo_refresh_minutes: float = 5.0


def load_operational_config() -> OperationalConfig:
    missing = [name for name in REQUIRED_HALO_ENV_VARS if not os.environ.get(name)]
    if missing:
        raise StartupError(f"Missing required env var(s): {', '.join(missing)}")
    try:
        qty_field_id = int(os.environ["HALO_ASSET_TAG_QTY_FIELD_ID"])
    except ValueError as exc:
        raise StartupError("HALO_ASSET_TAG_QTY_FIELD_ID must be an integer") from exc

    return OperationalConfig(
        halo_base_url=os.environ["HALO_BASE_URL"],
        halo_auth_url=os.environ["HALO_AUTH_URL"],
        halo_client_id=os.environ["HALO_CLIENT_ID"],
        halo_client_secret=os.environ["HALO_CLIENT_SECRET"],
        qty_field_id=qty_field_id,
        poll_interval_seconds=float(os.environ.get("POLL_INTERVAL_SECONDS", 15)),
        max_tags_per_asset=int(os.environ.get("MAX_TAGS_PER_ASSET", 5)),
        max_assets_per_poll=int(os.environ.get("MAX_ASSETS_PER_POLL", 25)),
        shadow_mode=env_bool("SHADOW_MODE", False),
        logo_refresh_minutes=float(os.environ.get("LOGO_REFRESH_MINUTES", 5)),
    )


def load_asset_tag_path(config_dir: Path = DEFAULT_CONFIG_DIR) -> str:
    fields_path = config_dir / "fields.yaml"
    fields_config = load_fields_config(fields_path) if fields_path.exists() else {}
    return fields_config.get("asset_tag", "inventory_number")


def load_layout_config_or_default(config_dir: Path = DEFAULT_CONFIG_DIR) -> LayoutConfig:
    layout_path = config_dir / "layout.yaml"
    return load_layout_config(layout_path) if layout_path.exists() else DEFAULT_LAYOUT_CONFIG


def touch_heartbeat(path: Path = HEARTBEAT_PATH) -> None:
    path.touch()


def render_job(printer_cfg, layout_config: LayoutConfig, logo_cache: LogoCache, tag_value: str, qty: int) -> str:
    """Build one ZPL job printing `qty` copies of `tag_value`. The QR
    payload and the printed text are always the same value (see the
    project spec, section 4.2 -- no URL, no prefix, just the tag number).
    Raises LayoutError if the tag can't be laid out legibly."""
    job_cfg = replace(printer_cfg, human_text=tag_value, barcode_value=tag_value, copies=qty)
    logo_cache.ensure_loaded()  # must happen before reading aspect_ratio -- see logo.py
    aspect_ratio = logo_cache.aspect_ratio
    layout = compute_layout(
        job_cfg, logo_aspect_ratio=aspect_ratio, layout_config=layout_config
    )
    logo_gf_field = (
        logo_cache.get_gf_field((layout.logo.w, layout.logo.h)) if aspect_ratio is not None else None
    )
    return build_zpl(job_cfg, logo_gf_field, layout=layout, layout_config=layout_config)


def run_once(
    op_cfg: OperationalConfig,
    printer_cfg,
    layout_config: LayoutConfig,
    logo_cache: LogoCache,
    halo_client: HaloClient,
    asset_tag_path: str,
) -> None:
    """One poll iteration. Never raises for expected failure modes (Halo
    outage, printer outage, a bad tag value) -- those are logged and the
    loop continues; only a caller-level bug should propagate out."""
    touch_heartbeat()

    try:
        assets = get_pending(halo_client, op_cfg.qty_field_id, asset_tag_path)
    except Exception as exc:  # noqa: BLE001 - a Halo outage must delay printing, never crash the loop
        logger.warning("get_pending() failed, will retry next poll: %s", exc)
        return

    assets = apply_batch_cap(assets, op_cfg.max_assets_per_poll)

    jobs: list[tuple] = []
    for asset in assets:
        if not asset.asset_tag:
            logger.debug("skipping asset %s -- no tag number yet", asset.id)
            continue

        clamped = clamp_qty(asset, op_cfg.max_tags_per_asset)
        try:
            zpl = render_job(printer_cfg, layout_config, logo_cache, clamped.asset_tag, clamped.qty)
        except LayoutError as exc:
            logger.error("cannot render tag for asset %s: %s", asset.id, exc)
            continue  # leave flagged -- a human must fix the value

        if op_cfg.shadow_mode:
            logger.info(
                "SHADOW: would print %d tag(s) for asset %s (%s)", clamped.qty, asset.id, asset.asset_tag
            )
            continue

        try:
            halo_client.claim(asset.id, op_cfg.qty_field_id)
        except Exception as exc:  # noqa: BLE001 - never print an asset whose claim didn't confirm
            logger.warning("claim failed for asset %s, retried next poll: %s", asset.id, exc)
            continue

        jobs.append((asset, zpl))

    if jobs:
        try:
            send_batch(
                [zpl for _, zpl in jobs],
                printer_cfg.printer_ip,
                printer_cfg.printer_port,
                timeout=printer_cfg.connect_timeout,
                retries=printer_cfg.retries,
            )
        except PrinterConnectionError as exc:
            logger.error("print failed for %d job(s): %s", len(jobs), exc)
            # already claimed -- no on-asset record either way, agent re-requests (see 3.1)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

    try:
        op_cfg = load_operational_config()
        printer_cfg = build_config([])
    except (StartupError, ValueError) as exc:
        logger.error("startup config error: %s", exc)
        return 2

    layout_config = load_layout_config_or_default()
    asset_tag_path = load_asset_tag_path()

    logger.info(
        "starting poll loop: interval=%ss max_tags_per_asset=%d max_assets_per_poll=%d shadow_mode=%s",
        op_cfg.poll_interval_seconds,
        op_cfg.max_tags_per_asset,
        op_cfg.max_assets_per_poll,
        op_cfg.shadow_mode,
    )
    if op_cfg.shadow_mode:
        logger.warning("SHADOW_MODE is on -- nothing will be claimed or printed")

    logo_cache = LogoCache(
        printer_cfg.logo_asset,
        method=printer_cfg.logo_method,
        refresh_seconds=op_cfg.logo_refresh_minutes * 60,
    )

    with HaloClient(
        op_cfg.halo_base_url, op_cfg.halo_auth_url, op_cfg.halo_client_id, op_cfg.halo_client_secret
    ) as halo_client:
        while True:
            run_once(op_cfg, printer_cfg, layout_config, logo_cache, halo_client, asset_tag_path)
            time.sleep(op_cfg.poll_interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
