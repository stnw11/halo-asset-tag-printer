from src.asset_source import get_pending


class FakeHaloClient:
    """Duck-typed stand-in for HaloClient -- get_pending() only needs
    iter_assets(), so a real HTTP-backed client isn't necessary here."""

    def __init__(self, assets):
        self._assets = assets

    def iter_assets(self):
        yield from self._assets


def make_asset(id, inventory_number="TAG-000001", field_182_value=None):
    fields = []
    if field_182_value is not None:
        fields.append({"id": 182, "name": "Asset Tag Print QTY", "value": field_182_value})
    return {"id": id, "inventory_number": inventory_number, "fields": fields}


def test_get_pending_returns_assets_with_positive_qty():
    assets = [make_asset(1, field_182_value="3"), make_asset(2, field_182_value="0")]
    client = FakeHaloClient(assets)
    pending = get_pending(client, qty_field_id=182, asset_tag_path="inventory_number")
    assert len(pending) == 1
    assert pending[0].id == 1
    assert pending[0].qty == 3


def test_get_pending_skips_assets_with_no_field_at_all():
    assets = [make_asset(1, field_182_value=None)]  # field never added to this asset type
    client = FakeHaloClient(assets)
    pending = get_pending(client, qty_field_id=182, asset_tag_path="inventory_number")
    assert pending == []


def test_get_pending_skips_empty_or_zero_value():
    assets = [make_asset(1, field_182_value=""), make_asset(2, field_182_value="0")]
    client = FakeHaloClient(assets)
    pending = get_pending(client, qty_field_id=182, asset_tag_path="inventory_number")
    assert pending == []


def test_get_pending_resolves_asset_tag_via_configured_path():
    assets = [make_asset(1, inventory_number="ABC00008", field_182_value="1")]
    client = FakeHaloClient(assets)
    pending = get_pending(client, qty_field_id=182, asset_tag_path="inventory_number")
    assert pending[0].asset_tag == "ABC00008"


def test_get_pending_resolves_asset_tag_via_assetfield_prefix():
    asset = make_asset(1, field_182_value="1")
    asset["fields"].append({"id": 200, "name": "Serial Number", "value": "SN-123"})
    client = FakeHaloClient([asset])
    pending = get_pending(client, qty_field_id=182, asset_tag_path="assetfield:200")
    assert pending[0].asset_tag == "SN-123"


def test_get_pending_malformed_qty_value_treated_as_zero():
    assets = [make_asset(1, field_182_value="not-a-number")]
    client = FakeHaloClient(assets)
    pending = get_pending(client, qty_field_id=182, asset_tag_path="inventory_number")
    assert pending == []


def test_get_pending_preserves_raw_asset_data():
    assets = [make_asset(1, field_182_value="2")]
    client = FakeHaloClient(assets)
    pending = get_pending(client, qty_field_id=182, asset_tag_path="inventory_number")
    assert pending[0].raw == assets[0]
