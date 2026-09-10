from src.guards import apply_batch_cap, clamp_qty
from src.models import Asset


def make_asset(id=1, qty=1, asset_tag="TAG-000001"):
    return Asset(id=id, qty=qty, asset_tag=asset_tag, raw={})


def test_clamp_qty_leaves_under_cap_unchanged():
    asset = make_asset(qty=3)
    result = clamp_qty(asset, max_tags_per_asset=5)
    assert result.qty == 3


def test_clamp_qty_leaves_exactly_at_cap_unchanged():
    asset = make_asset(qty=5)
    result = clamp_qty(asset, max_tags_per_asset=5)
    assert result.qty == 5


def test_clamp_qty_caps_over_limit_and_logs(caplog):
    asset = make_asset(id=42, qty=50)
    with caplog.at_level("WARNING"):
        result = clamp_qty(asset, max_tags_per_asset=5)
    assert result.qty == 5
    assert "42" in caplog.text
    assert "capped to 5" in caplog.text


def test_clamp_qty_does_not_mutate_original():
    asset = make_asset(qty=50)
    clamp_qty(asset, max_tags_per_asset=5)
    assert asset.qty == 50  # Asset is frozen -- original is untouched


def test_apply_batch_cap_under_limit_returns_all():
    assets = [make_asset(id=i) for i in range(3)]
    result = apply_batch_cap(assets, max_assets_per_poll=25)
    assert result == assets


def test_apply_batch_cap_over_limit_truncates_and_logs(caplog):
    assets = [make_asset(id=i) for i in range(30)]
    with caplog.at_level("INFO"):
        result = apply_batch_cap(assets, max_assets_per_poll=25)
    assert len(result) == 25
    assert result == assets[:25]
    assert "30 assets pending" in caplog.text


def test_apply_batch_cap_exactly_at_limit_returns_all():
    assets = [make_asset(id=i) for i in range(25)]
    result = apply_batch_cap(assets, max_assets_per_poll=25)
    assert len(result) == 25
