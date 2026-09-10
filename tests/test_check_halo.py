import httpx
import respx

from tools.check_halo import main

BASE_URL = "https://example.haloitsm.com"
AUTH_URL = "https://example.haloitsm.com/auth/token"


def _set_required_env(monkeypatch):
    monkeypatch.setenv("HALO_BASE_URL", BASE_URL)
    monkeypatch.setenv("HALO_AUTH_URL", AUTH_URL)
    monkeypatch.setenv("HALO_CLIENT_ID", "client-id")
    monkeypatch.setenv("HALO_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("HALO_ASSET_TAG_QTY_FIELD_ID", "182")


def test_missing_env_vars_returns_nonzero(monkeypatch, capsys):
    # load_dotenv() with no path searches from the *calling module's* file
    # location, not the process cwd -- so chdir alone wouldn't stop it
    # from finding this repo's real local .env. Patch it to a no-op so
    # this test controls os.environ directly, same isolation concern as
    # test_config.py's build_config tests.
    monkeypatch.setattr("tools.check_halo.load_dotenv", lambda *a, **k: None)
    for name in [
        "HALO_BASE_URL", "HALO_AUTH_URL", "HALO_CLIENT_ID",
        "HALO_CLIENT_SECRET", "HALO_ASSET_TAG_QTY_FIELD_ID",
    ]:
        monkeypatch.delenv(name, raising=False)

    rc = main([])
    assert rc == 2
    assert "Missing required env var" in capsys.readouterr().err


@respx.mock
def test_successful_round_trip_reports_pending_assets(monkeypatch, tmp_path, capsys):
    _set_required_env(monkeypatch)
    monkeypatch.chdir(tmp_path)  # no config/fields.yaml here -> falls back to inventory_number

    respx.post(AUTH_URL).mock(return_value=httpx.Response(200, json={"access_token": "t", "expires_in": 3600}))
    respx.get(f"{BASE_URL}/api/Asset").mock(
        return_value=httpx.Response(
            200,
            json={
                "page_no": 1,
                "page_size": 200,
                "record_count": 1,
                "assets": [
                    {
                        "id": 32,
                        "inventory_number": "ABC00008",
                        "fields": [{"id": 182, "value": "2"}],
                    }
                ],
            },
        )
    )

    rc = main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Token fetch: OK" in out
    assert "1 asset(s) pending" in out
    assert "ABC00008" in out
    assert "qty=2" in out


@respx.mock
def test_token_failure_returns_nonzero(monkeypatch, capsys):
    _set_required_env(monkeypatch)
    respx.post(AUTH_URL).mock(return_value=httpx.Response(401, json={"error": "invalid_client"}))

    rc = main([])
    assert rc == 1
    assert "Token fetch failed" in capsys.readouterr().err
