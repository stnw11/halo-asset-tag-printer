import pytest
import yaml

from src.config import StartupError, build_config


def test_defaults_are_neutral_placeholders(tmp_path):
    # config_dir=tmp_path isolates this from whatever config/printers.yaml
    # (if any) happens to exist locally on the machine running the tests.
    cfg = build_config([], config_dir=tmp_path)
    assert cfg.printer_ip == "192.0.2.10"  # RFC 5737 doc address, not a real printer
    assert cfg.printer_port == 9100
    assert cfg.dpi == 300
    assert cfg.label_width_in == 0.5
    assert cfg.label_height_in == 2.0
    assert cfg.media_sensing == "gap"
    assert cfg.barcode_value == "TAG-0123"
    assert cfg.human_text == "TAG-0123"
    assert cfg.logo_asset == "assets/placeholder_logo.png"
    assert cfg.copies == 1
    assert cfg.dry_run is False


def test_env_overrides_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("PRINTER_IP", "10.0.0.5")
    monkeypatch.setenv("DPI", "203")
    monkeypatch.setenv("DARKNESS", "20")
    monkeypatch.setenv("MEDIA_SENSING", "MARK")
    monkeypatch.setenv("DRY_RUN", "1")
    cfg = build_config([], config_dir=tmp_path)
    assert cfg.printer_ip == "10.0.0.5"
    assert cfg.dpi == 203
    assert cfg.darkness == 20
    assert cfg.media_sensing == "mark"  # lowercased
    assert cfg.dry_run is True


def test_cli_overrides_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PRINTER_IP", "10.0.0.5")
    cfg = build_config(["--printer-ip", "192.168.1.50"], config_dir=tmp_path)
    assert cfg.printer_ip == "192.168.1.50"


def test_cli_overrides_darkness_and_speed(tmp_path):
    cfg = build_config(["--darkness", "8", "--print-speed", "3"], config_dir=tmp_path)
    assert cfg.darkness == 8
    assert cfg.print_speed == 3


@pytest.mark.parametrize(
    "argv",
    [
        ["--darkness", "31"],
        ["--darkness", "-1"],
        ["--print-speed", "0"],
        ["--print-speed", "20"],
        ["--copies", "0"],
        ["--barcode-value", ""],
        ["--human-text", ""],
    ],
)
def test_invalid_config_raises(tmp_path, argv):
    with pytest.raises(ValueError):
        build_config(argv, config_dir=tmp_path)


def test_invalid_media_sensing_rejected_by_argparse(tmp_path):
    with pytest.raises(SystemExit):
        build_config(["--media-sensing", "bogus"], config_dir=tmp_path)


def test_dry_run_flag_from_cli(tmp_path):
    cfg = build_config(["--dry-run"], config_dir=tmp_path)
    assert cfg.dry_run is True


# --- config/printers.yaml loading (build order step 4) -------------------

def _write_printers_yaml(config_dir, printers: dict):
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "printers.yaml").write_text(yaml.safe_dump({"printers": printers}))


def test_no_printers_yaml_falls_back_to_neutral_defaults(tmp_path):
    # tools/print_test_tag.py is a bring-up tool -- it must work with zero
    # setup, not require config/printers.yaml to exist first.
    cfg = build_config([], config_dir=tmp_path)
    assert cfg.printer_ip == "192.0.2.10"


def test_printers_yaml_supplies_hardware_defaults(tmp_path):
    _write_printers_yaml(
        tmp_path,
        {"default": {"ip": "10.0.0.50", "port": 9200, "dpi": 203, "darkness": 20, "media_type": "direct_thermal"}},
    )
    cfg = build_config([], config_dir=tmp_path)
    assert cfg.printer_ip == "10.0.0.50"
    assert cfg.printer_port == 9200
    assert cfg.dpi == 203
    assert cfg.darkness == 20
    assert cfg.media_type == "direct_thermal"


def test_unedited_example_ip_in_printers_yaml_raises_startup_error(tmp_path):
    _write_printers_yaml(tmp_path, {"default": {"ip": "192.0.2.10"}})
    with pytest.raises(StartupError, match="example printer IP"):
        build_config([], config_dir=tmp_path)


def test_missing_printer_name_raises_startup_error(tmp_path, monkeypatch):
    _write_printers_yaml(tmp_path, {"default": {"ip": "10.0.0.50"}})
    monkeypatch.setenv("PRINTER_NAME", "shop")
    with pytest.raises(StartupError, match="not found"):
        build_config([], config_dir=tmp_path)


def test_malformed_printers_yaml_raises_startup_error(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "printers.yaml").write_text("printers: [this is not a mapping")
    with pytest.raises(StartupError, match="malformed YAML"):
        build_config([], config_dir=tmp_path)


def test_env_var_overrides_printers_yaml(tmp_path, monkeypatch):
    _write_printers_yaml(tmp_path, {"default": {"ip": "10.0.0.50", "darkness": 20}})
    monkeypatch.setenv("DARKNESS", "8")
    cfg = build_config([], config_dir=tmp_path)
    assert cfg.printer_ip == "10.0.0.50"  # from YAML, not overridden
    assert cfg.darkness == 8  # env var wins over YAML


def test_cli_flag_overrides_printers_yaml(tmp_path):
    _write_printers_yaml(tmp_path, {"default": {"ip": "10.0.0.50", "darkness": 20}})
    cfg = build_config(["--darkness", "5"], config_dir=tmp_path)
    assert cfg.darkness == 5  # CLI wins over YAML


def test_env_var_selects_named_printer(tmp_path, monkeypatch):
    _write_printers_yaml(
        tmp_path,
        {"default": {"ip": "10.0.0.50"}, "shop": {"ip": "10.0.0.60"}},
    )
    monkeypatch.setenv("PRINTER_NAME", "shop")
    cfg = build_config([], config_dir=tmp_path)
    assert cfg.printer_ip == "10.0.0.60"
