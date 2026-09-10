"""get_pending() -- the only place the "which assets need a tag printed"
business rule lives. src/halo_client.py owns the raw Halo API mechanics
this builds on; this module owns the domain question of what counts as
pending.
"""
from __future__ import annotations

from .fields import resolve_asset_field, resolve_path
from .halo_client import HaloClient
from .models import Asset


def get_pending(client: HaloClient, qty_field_id: int, asset_tag_path: str) -> list[Asset]:
    """Fetch every asset in the tenant and return those with a positive
    print-qty value.

    No server-side filter-by-field-value exists for Assets (see
    HaloClient.iter_assets), so this walks the full list -- the qty
    field's value lives at a fixed numeric Asset Field id (qty_field_id),
    read directly rather than through fields.yaml's generic path mapping,
    matching how it's configured (HALO_ASSET_TAG_QTY_FIELD_ID, not
    fields.yaml -- see the project spec, section 4.2).
    """
    pending = []
    for raw in client.iter_assets():
        qty_str = resolve_asset_field(raw, qty_field_id)
        try:
            qty = int(qty_str) if qty_str else 0
        except ValueError:
            qty = 0
        if qty > 0:
            asset_tag = resolve_path(raw, asset_tag_path)
            pending.append(Asset(id=raw.get("id"), qty=qty, asset_tag=asset_tag, raw=raw))
    return pending
