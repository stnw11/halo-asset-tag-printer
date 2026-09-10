import src.main as main_module
from src.config import PrinterConfig
from src.guards import apply_batch_cap
from src.logo import LogoCache
from src.models import Asset
from src.printer_client import PrinterConnectionError
from src.zpl_template import DEFAULT_LAYOUT_CONFIG


class FakeHaloClient:
    def __init__(self, assets_to_return=None, claim_should_fail=False):
        self._assets = assets_to_return or []
        self.claim_should_fail = claim_should_fail
        self.claimed_ids = []

    def iter_assets(self):
        yield from self._assets

    def claim(self, asset_id, qty_field_id):
        if self.claim_should_fail:
            raise RuntimeError("simulated claim failure")
        self.claimed_ids.append(asset_id)


def make_asset_json(id, inventory_number="TAG-0001", qty=1):
    return {
        "id": id,
        "inventory_number": inventory_number,
        "fields": [{"id": 182, "value": str(qty)}],
    }


def make_op_cfg(**overrides):
    defaults = dict(
        halo_base_url="https://example.haloitsm.com",
        halo_auth_url="https://example.haloitsm.com/auth/token",
        halo_client_id="id",
        halo_client_secret="secret",
        qty_field_id=182,
        poll_interval_seconds=15.0,
        max_tags_per_asset=5,
        max_assets_per_poll=25,
        shadow_mode=False,
    )
    defaults.update(overrides)
    return main_module.OperationalConfig(**defaults)


def run(monkeypatch, tmp_path, halo_client, printer_cfg=None, op_cfg=None, send_batch_mock=None):
    heartbeat_path = tmp_path / "heartbeat"
    monkeypatch.setattr(main_module, "HEARTBEAT_PATH", heartbeat_path)
    monkeypatch.setattr(main_module, "touch_heartbeat", lambda path=heartbeat_path: heartbeat_path.touch())

    calls = []
    monkeypatch.setattr(
        main_module,
        "send_batch",
        send_batch_mock or (lambda zpl_jobs, ip, port, timeout, retries: calls.append(zpl_jobs)),
    )

    printer_cfg = printer_cfg or PrinterConfig()
    op_cfg = op_cfg or make_op_cfg()
    logo_cache = LogoCache(None)  # no logo -- keeps rendering simple for these control-flow tests

    main_module.run_once(op_cfg, printer_cfg, DEFAULT_LAYOUT_CONFIG, logo_cache, halo_client, "inventory_number")
    return calls, heartbeat_path


def test_touches_heartbeat_every_iteration(monkeypatch, tmp_path):
    client = FakeHaloClient([])
    _, heartbeat_path = run(monkeypatch, tmp_path, client)
    assert heartbeat_path.exists()


def test_get_pending_failure_does_not_raise(monkeypatch, tmp_path):
    client = FakeHaloClient([])
    monkeypatch.setattr(main_module, "get_pending", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("halo down")))
    calls, _ = run(monkeypatch, tmp_path, client)
    assert calls == []  # nothing printed, and nothing raised


def test_blank_tag_is_skipped_without_claiming(monkeypatch, tmp_path):
    asset_json = make_asset_json(1, inventory_number="", qty=1)
    client = FakeHaloClient([asset_json])
    calls, _ = run(monkeypatch, tmp_path, client)
    assert client.claimed_ids == []
    assert calls == []


def test_successful_asset_is_claimed_then_printed(monkeypatch, tmp_path):
    asset_json = make_asset_json(1, inventory_number="TAG-0001", qty=2)
    client = FakeHaloClient([asset_json])
    calls, _ = run(monkeypatch, tmp_path, client)
    assert client.claimed_ids == [1]
    assert len(calls) == 1
    assert len(calls[0]) == 1  # one ZPL job in the batch
    assert "TAG-0001" in calls[0][0]


def test_claim_failure_prevents_printing(monkeypatch, tmp_path):
    asset_json = make_asset_json(1, qty=1)
    client = FakeHaloClient([asset_json], claim_should_fail=True)
    calls, _ = run(monkeypatch, tmp_path, client)
    assert calls == []


def test_shadow_mode_neither_claims_nor_prints(monkeypatch, tmp_path):
    asset_json = make_asset_json(1, qty=1)
    client = FakeHaloClient([asset_json])
    op_cfg = make_op_cfg(shadow_mode=True)
    calls, _ = run(monkeypatch, tmp_path, client, op_cfg=op_cfg)
    assert client.claimed_ids == []
    assert calls == []


def test_qty_over_cap_is_clamped(monkeypatch, tmp_path):
    asset_json = make_asset_json(1, qty=50)
    client = FakeHaloClient([asset_json])
    op_cfg = make_op_cfg(max_tags_per_asset=5)
    calls, _ = run(monkeypatch, tmp_path, client, op_cfg=op_cfg)
    assert "^PQ5" in calls[0][0]  # ZPL's own copies command, not 5 repeated ^XA blocks


def test_layout_error_skips_asset_without_claiming(monkeypatch, tmp_path):
    long_tag = "TAG-" + "0" * 40  # forces LayoutError (below legibility floor)
    asset_json = make_asset_json(1, inventory_number=long_tag, qty=1)
    client = FakeHaloClient([asset_json])
    calls, _ = run(monkeypatch, tmp_path, client)
    assert client.claimed_ids == []
    assert calls == []


def test_print_failure_after_claim_does_not_raise(monkeypatch, tmp_path):
    asset_json = make_asset_json(1, qty=1)
    client = FakeHaloClient([asset_json])

    def failing_send_batch(*a, **k):
        raise PrinterConnectionError("printer unplugged")

    calls, _ = run(monkeypatch, tmp_path, client, send_batch_mock=failing_send_batch)
    assert client.claimed_ids == [1]  # already claimed before the print attempt
    assert calls == []


def test_batch_cap_applied_before_processing(monkeypatch, tmp_path):
    assets = [make_asset_json(i, inventory_number=f"TAG-{i:03d}", qty=1) for i in range(30)]
    client = FakeHaloClient(assets)
    op_cfg = make_op_cfg(max_assets_per_poll=25)
    calls, _ = run(monkeypatch, tmp_path, client, op_cfg=op_cfg)
    assert len(client.claimed_ids) == 25


def test_load_operational_config_missing_vars_raises(monkeypatch):
    for name in main_module.REQUIRED_HALO_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    try:
        main_module.load_operational_config()
        assert False, "expected StartupError"
    except Exception as exc:
        assert "Missing required env var" in str(exc)


def test_load_operational_config_reads_env(monkeypatch):
    monkeypatch.setenv("HALO_BASE_URL", "https://x.haloitsm.com")
    monkeypatch.setenv("HALO_AUTH_URL", "https://x.haloitsm.com/auth/token")
    monkeypatch.setenv("HALO_CLIENT_ID", "id")
    monkeypatch.setenv("HALO_CLIENT_SECRET", "secret")
    monkeypatch.setenv("HALO_ASSET_TAG_QTY_FIELD_ID", "182")
    monkeypatch.setenv("MAX_TAGS_PER_ASSET", "7")
    cfg = main_module.load_operational_config()
    assert cfg.qty_field_id == 182
    assert cfg.max_tags_per_asset == 7
