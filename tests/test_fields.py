from src.fields import load_fields_config, resolve_asset_field, resolve_fields, resolve_path


def test_resolve_dotted_path():
    asset = {"inventory_number": "TAG-000123"}
    assert resolve_path(asset, "inventory_number") == "TAG-000123"


def test_resolve_nested_dotted_path():
    asset = {"site": {"name": "Warehouse 2"}}
    assert resolve_path(asset, "site.name") == "Warehouse 2"


def test_resolve_dotted_path_missing_returns_empty_string():
    asset = {"inventory_number": "TAG-000123"}
    assert resolve_path(asset, "does_not_exist") == ""
    assert resolve_path(asset, "site.name") == ""


def test_resolve_custom_field_by_name():
    asset = {"customfields": [{"name": "CFAssetTagPrintQTY", "value": "3"}]}
    assert resolve_path(asset, "cf:CFAssetTagPrintQTY") == "3"


def test_resolve_custom_field_missing_returns_empty_string():
    asset = {"customfields": [{"name": "SomeOtherField", "value": "x"}]}
    assert resolve_path(asset, "cf:CFAssetTagPrintQTY") == ""


def test_resolve_custom_field_no_customfields_key():
    assert resolve_path({}, "cf:CFAssetTagPrintQTY") == ""


def test_resolve_null_value_is_empty_string():
    asset = {"inventory_number": None}
    assert resolve_path(asset, "inventory_number") == ""


def test_resolve_non_string_value_is_stringified():
    asset = {"inventory_number": 12345}
    assert resolve_path(asset, "inventory_number") == "12345"


def test_resolve_fields_applies_every_mapping():
    asset = {"inventory_number": "TAG-000123", "site": {"name": "HQ"}}
    resolved = resolve_fields(asset, {"asset_tag": "inventory_number", "site": "site.name"})
    assert resolved == {"asset_tag": "TAG-000123", "site": "HQ"}


def test_load_fields_config(tmp_path):
    path = tmp_path / "fields.yaml"
    path.write_text("fields:\n  asset_tag: inventory_number\n")
    assert load_fields_config(path) == {"asset_tag": "inventory_number"}


def test_load_fields_config_empty_file_returns_empty_dict(tmp_path):
    path = tmp_path / "fields.yaml"
    path.write_text("")
    assert load_fields_config(path) == {}


# --- Asset Fields: "fields" array, matched by numeric id (not "cf:") -----

def test_resolve_asset_field_by_id():
    asset = {"fields": [{"id": 182, "name": "Asset Tag Print QTY", "value": "5"}]}
    assert resolve_asset_field(asset, 182) == "5"


def test_resolve_asset_field_missing_id_returns_empty_string():
    asset = {"fields": [{"id": 182, "value": "5"}]}
    assert resolve_asset_field(asset, 999) == ""


def test_resolve_asset_field_no_fields_key():
    assert resolve_asset_field({}, 182) == ""


def test_resolve_asset_field_accepts_string_id():
    asset = {"fields": [{"id": 182, "value": "5"}]}
    assert resolve_asset_field(asset, "182") == "5"


def test_resolve_asset_field_invalid_id_returns_empty_string():
    assert resolve_asset_field({"fields": []}, "not-a-number") == ""


def test_resolve_path_assetfield_prefix():
    asset = {"fields": [{"id": 182, "value": "5"}]}
    assert resolve_path(asset, "assetfield:182") == "5"


def test_resolve_path_assetfield_prefix_distinct_from_cf():
    # Same asset can carry both an Asset Field (fields array) and a
    # Custom Field (customfields array) -- they must not be conflated.
    asset = {
        "fields": [{"id": 182, "value": "5"}],
        "customfields": [{"id": 182, "name": "SomeOtherField", "value": "wrong"}],
    }
    assert resolve_path(asset, "assetfield:182") == "5"
    assert resolve_path(asset, "cf:SomeOtherField") == "wrong"
