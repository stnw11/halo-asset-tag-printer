"""Resolves config/fields.yaml's path syntax against a Halo asset JSON
payload. Adapted from the Halo-Ticket-Label-Printer sibling project's
fields.py, which is already proven against a live Halo tenant -- same
dotted-path and "cf:" custom-field conventions.

Assets add a third lookup Tickets don't have: "assetfield:<id>", matching
by numeric id in the asset's `fields` array. This is a genuinely different
mechanism from "cf:" custom fields (Halo Assets carry both `fields` and
`customfields` as separate arrays -- confirmed against a live tenant), and
Asset Fields are matched by id, not by name: their "name" is just a
human display label (e.g. "Asset Tag Print QTY" with spaces), not a
stable code-style identifier the way ticket custom-field names are.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def resolve_path(asset_json: dict, path: str) -> str:
    """Resolve one path against asset_json.

    - dotted paths walk nested objects: "site.name"
    - a "cf:<name>" prefix looks up a custom field by name in the asset's
      customfields array (Halo returns custom fields as a list of
      {name, value} objects, not flat keys)
    - an "assetfield:<id>" prefix looks up an Asset Field by numeric id in
      the asset's fields array (see module docstring)
    - a missing/null value resolves to "" so the caller can just treat it
      as absent
    """
    if path.startswith("cf:"):
        return _resolve_custom_field(asset_json, path[3:])
    if path.startswith("assetfield:"):
        return resolve_asset_field(asset_json, path[len("assetfield:"):])
    return _resolve_dotted_path(asset_json, path)


def resolve_asset_field(asset_json: dict, field_id: int | str) -> str:
    """Look up an Asset Field by numeric id in the asset's `fields` array
    (distinct from `customfields` -- see module docstring). Used both via
    the "assetfield:" path prefix and directly by halo_client.py for the
    print-qty field, which is read by id rather than through fields.yaml's
    generic mapping (see config/fields.example.yaml)."""
    try:
        target_id = int(field_id)
    except (TypeError, ValueError):
        return ""
    for f in asset_json.get("fields") or []:
        if isinstance(f, dict) and f.get("id") == target_id:
            return _stringify(f.get("value"))
    return ""


def _resolve_dotted_path(obj: Any, path: str) -> str:
    current = obj
    for part in path.split("."):
        if not isinstance(current, dict):
            return ""
        current = current.get(part)
    return _stringify(current)


def _resolve_custom_field(asset_json: dict, field_name: str) -> str:
    custom_fields = asset_json.get("customfields") or []
    for cf in custom_fields:
        if isinstance(cf, dict) and cf.get("name") == field_name:
            return _stringify(cf.get("value"))
    return ""


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def resolve_fields(asset_json: dict, fields_config: dict[str, str]) -> dict[str, str]:
    """Resolve every entry in fields_config against asset_json."""
    return {var: resolve_path(asset_json, path) for var, path in fields_config.items()}


def load_fields_config(config_path: str | Path) -> dict[str, str]:
    with open(config_path, "r") as f:
        data = yaml.safe_load(f) or {}
    return data.get("fields") or {}
