import httpx
import pytest
import respx

from src.halo_client import HaloClient

BASE_URL = "https://example.haloitsm.com"
AUTH_URL = "https://example.haloitsm.com/auth/token"


def make_client() -> HaloClient:
    return HaloClient(BASE_URL, AUTH_URL, "client-id", "client-secret")


@respx.mock
def test_get_token_fetches_and_caches():
    route = respx.post(AUTH_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})
    )
    client = make_client()
    assert client.get_token() == "tok-1"
    assert client.get_token() == "tok-1"  # second call reuses the cached token
    assert route.call_count == 1


@respx.mock
def test_get_token_refreshes_after_expiry():
    respx.post(AUTH_URL).mock(
        side_effect=[
            httpx.Response(200, json={"access_token": "tok-1", "expires_in": 0}),
            httpx.Response(200, json={"access_token": "tok-2", "expires_in": 3600}),
        ]
    )
    client = make_client()
    assert client.get_token() == "tok-1"
    assert client.get_token() == "tok-2"  # expired (expires_in=0 minus the safety margin) -> refetch


@respx.mock
def test_token_request_sends_user_agent():
    route = respx.post(AUTH_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})
    )
    make_client().get_token()
    assert route.calls.last.request.headers["User-Agent"].startswith("Halo-Asset-Tag-Printer/")


@respx.mock
def test_list_assets_page_sends_expected_params_and_auth():
    respx.post(AUTH_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})
    )
    route = respx.get(f"{BASE_URL}/api/Asset").mock(
        return_value=httpx.Response(200, json={"page_no": 1, "page_size": 200, "record_count": 0, "assets": []})
    )
    client = make_client()
    client.list_assets_page(1)
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer tok-1"
    assert request.headers["User-Agent"].startswith("Halo-Asset-Tag-Printer/")
    assert request.url.params["includeassetfields"] == "true"
    assert request.url.params["page_no"] == "1"


@respx.mock
def test_iter_assets_single_page():
    respx.post(AUTH_URL).mock(return_value=httpx.Response(200, json={"access_token": "t", "expires_in": 3600}))
    respx.get(f"{BASE_URL}/api/Asset").mock(
        return_value=httpx.Response(
            200,
            json={"page_no": 1, "page_size": 200, "record_count": 2, "assets": [{"id": 1}, {"id": 2}]},
        )
    )
    client = make_client()
    assets = list(client.iter_assets())
    assert [a["id"] for a in assets] == [1, 2]


@respx.mock
def test_iter_assets_paginates_across_multiple_pages():
    respx.post(AUTH_URL).mock(return_value=httpx.Response(200, json={"access_token": "t", "expires_in": 3600}))
    pages = {
        1: {"page_no": 1, "page_size": 2, "record_count": 5, "assets": [{"id": 1}, {"id": 2}]},
        2: {"page_no": 2, "page_size": 2, "record_count": 5, "assets": [{"id": 3}, {"id": 4}]},
        3: {"page_no": 3, "page_size": 2, "record_count": 5, "assets": [{"id": 5}]},
    }

    def responder(request):
        page_no = int(request.url.params["page_no"])
        return httpx.Response(200, json=pages[page_no])

    respx.get(f"{BASE_URL}/api/Asset").mock(side_effect=responder)
    client = make_client()
    assets = list(client.iter_assets(page_size=2))
    assert [a["id"] for a in assets] == [1, 2, 3, 4, 5]


@respx.mock
def test_iter_assets_handles_server_capped_page_size():
    # Regression test: the live tenant silently caps page_size at 50
    # regardless of what's requested, and echoes the *effective* size back
    # in the response, not the requested one. Requesting 200 but getting
    # 50-record pages must still walk every page, not stop after one
    # based on the requested size.
    respx.post(AUTH_URL).mock(return_value=httpx.Response(200, json={"access_token": "t", "expires_in": 3600}))
    pages = {
        1: {"page_no": 1, "page_size": 50, "record_count": 68, "assets": [{"id": i} for i in range(1, 51)]},
        2: {"page_no": 2, "page_size": 50, "record_count": 68, "assets": [{"id": i} for i in range(51, 69)]},
    }

    def responder(request):
        page_no = int(request.url.params["page_no"])
        return httpx.Response(200, json=pages[page_no])

    respx.get(f"{BASE_URL}/api/Asset").mock(side_effect=responder)
    client = make_client()
    assets = list(client.iter_assets(page_size=200))  # request 200, server caps at 50
    assert len(assets) == 68
    assert [a["id"] for a in assets] == list(range(1, 69))


@respx.mock
def test_iter_assets_stops_on_empty_page_even_if_record_count_implies_more():
    respx.post(AUTH_URL).mock(return_value=httpx.Response(200, json={"access_token": "t", "expires_in": 3600}))
    respx.get(f"{BASE_URL}/api/Asset").mock(
        return_value=httpx.Response(200, json={"page_no": 1, "page_size": 200, "record_count": 50, "assets": []})
    )
    client = make_client()
    assert list(client.iter_assets()) == []


@respx.mock
def test_claim_posts_expected_payload():
    respx.post(AUTH_URL).mock(return_value=httpx.Response(200, json={"access_token": "t", "expires_in": 3600}))
    route = respx.post(f"{BASE_URL}/api/Asset").mock(return_value=httpx.Response(201, json={"id": 32}))
    client = make_client()
    client.claim(asset_id=32, qty_field_id=182)
    import json

    body = json.loads(route.calls.last.request.content)
    assert body == [{"id": 32, "fields": [{"id": 182, "value": "0"}]}]


@respx.mock
def test_claim_raises_on_http_error():
    respx.post(AUTH_URL).mock(return_value=httpx.Response(200, json={"access_token": "t", "expires_in": 3600}))
    respx.post(f"{BASE_URL}/api/Asset").mock(return_value=httpx.Response(403))
    client = make_client()
    with pytest.raises(httpx.HTTPStatusError):
        client.claim(asset_id=32, qty_field_id=182)


def test_close_closes_underlying_client():
    client = make_client()
    client.close()
    assert client._client.is_closed


def test_context_manager_closes_on_exit():
    with make_client() as client:
        pass
    assert client._client.is_closed
