"""Bring-up and troubleshooting CLI: verify the Halo auth + get_pending()
round-trip. Prints nothing physical -- purely a connectivity/config check,
the Halo-side equivalent of tools/print_test_tag.py.

Run from the repo root:
    python -m tools.check_halo
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from src.asset_source import get_pending
from src.config import DEFAULT_CONFIG_DIR
from src.fields import load_fields_config
from src.halo_client import HaloClient

REQUIRED_ENV_VARS = [
    "HALO_BASE_URL",
    "HALO_AUTH_URL",
    "HALO_CLIENT_ID",
    "HALO_CLIENT_SECRET",
    "HALO_ASSET_TAG_QTY_FIELD_ID",
]


def main(argv: list[str] | None = None) -> int:
    load_dotenv()

    missing = [name for name in REQUIRED_ENV_VARS if not os.environ.get(name)]
    if missing:
        print(f"Missing required env var(s): {', '.join(missing)}", file=sys.stderr)
        return 2

    try:
        qty_field_id = int(os.environ["HALO_ASSET_TAG_QTY_FIELD_ID"])
    except ValueError:
        print("HALO_ASSET_TAG_QTY_FIELD_ID must be an integer", file=sys.stderr)
        return 2

    fields_path = DEFAULT_CONFIG_DIR / "fields.yaml"
    fields_config = load_fields_config(fields_path) if fields_path.exists() else {}
    asset_tag_path = fields_config.get("asset_tag", "inventory_number")
    if not fields_path.exists():
        print(
            f"Note: {fields_path} doesn't exist yet -- falling back to "
            f"asset_tag={asset_tag_path!r}. Copy config/fields.example.yaml "
            "to set the real mapping.",
            file=sys.stderr,
        )

    with HaloClient(
        base_url=os.environ["HALO_BASE_URL"],
        auth_url=os.environ["HALO_AUTH_URL"],
        client_id=os.environ["HALO_CLIENT_ID"],
        client_secret=os.environ["HALO_CLIENT_SECRET"],
    ) as client:
        try:
            client.get_token()
        except Exception as exc:  # noqa: BLE001 - surface any auth failure clearly to the operator
            print(f"Token fetch failed: {exc}", file=sys.stderr)
            return 1
        print("Token fetch: OK")

        try:
            pending = get_pending(client, qty_field_id, asset_tag_path)
        except Exception as exc:  # noqa: BLE001 - surface any API failure clearly to the operator
            print(f"get_pending() failed: {exc}", file=sys.stderr)
            return 1

    print(f"get_pending(): OK -- {len(pending)} asset(s) pending (field id {qty_field_id}, tag path {asset_tag_path!r})")
    for asset in pending:
        print(f"  id={asset.id} asset_tag={asset.asset_tag!r} qty={asset.qty}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
