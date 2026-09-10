"""Per-asset and per-poll guardrails -- see the project spec, section 3.4.

Deliberately lighter than an auto-printing design would need: every job
traces back to a person who set the qty field, so what remains only
guards against fat-fingering (a "50" typed into the qty field) and an
over-broad mass-update (a list operation that selected more than
intended). An earlier draft also had an hourly safety-valve guard
(MAX_TAGS_PER_HOUR) -- dropped; see the spec for why (a rolling-window
counter and a "stay paused until restart" state that a container's own
restart policy would silently undo weren't worth it at this project's
scale).
"""
from __future__ import annotations

import logging
from dataclasses import replace

from .models import Asset

logger = logging.getLogger(__name__)


def clamp_qty(asset: Asset, max_tags_per_asset: int) -> Asset:
    """Clamp asset.qty to max_tags_per_asset, logging if the request was
    over the cap. Returns a new Asset -- callers should render/print using
    the returned value's qty, not the original."""
    if asset.qty > max_tags_per_asset:
        logger.warning(
            "asset %s requested qty %d, capped to %d (MAX_TAGS_PER_ASSET)",
            asset.id,
            asset.qty,
            max_tags_per_asset,
        )
        return replace(asset, qty=max_tags_per_asset)
    return asset


def apply_batch_cap(assets: list[Asset], max_assets_per_poll: int) -> list[Asset]:
    """Return at most max_assets_per_poll assets, logging if more were
    pending. The remainder isn't dropped -- it simply waits and gets
    picked up on a later poll, since nothing here claims or prints
    anything."""
    if len(assets) > max_assets_per_poll:
        logger.info(
            "%d assets pending, processing %d this poll (MAX_ASSETS_PER_POLL); remainder waits for next poll",
            len(assets),
            max_assets_per_poll,
        )
        return assets[:max_assets_per_poll]
    return assets
