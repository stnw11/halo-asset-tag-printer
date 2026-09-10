"""OAuth2 client-credentials token management and asset read/claim
operations against HaloITSM.

Deliberately does not post an audit note back to the asset on success or
failure -- outcome is only visible in the service's own logs, matching the
sibling ticket printer's precedent (see 3.1's rationale for dropping a
separate "last printed" stamp).

Login mode matters more than it looks: confirmed against a live tenant
that an API application using "Application identity" cannot resolve Asset
visibility at all in this Halo version, regardless of what permissions are
granted on the application itself -- Asset visibility is gated by the
underlying Agent's own role permissions instead. The client credentials
this class uses must belong to an application logged in as a dedicated
Agent account, not Application identity (see the project spec, section
4.9).
"""
from __future__ import annotations

import logging
import time
from collections.abc import Iterator

import httpx

logger = logging.getLogger(__name__)

TOKEN_EXPIRY_MARGIN_SECONDS = 60
DEFAULT_USER_AGENT = "Halo-Asset-Tag-Printer/1.0 (+https://github.com/example/Halo-Asset-Tag-Printer)"
DEFAULT_PAGE_SIZE = 200


class HaloClient:
    def __init__(
        self,
        base_url: str,
        auth_url: str,
        client_id: str,
        client_secret: str,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = 10.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.auth_url = auth_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.user_agent = user_agent
        self.timeout = timeout
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._client = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HaloClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def _headers(self, authorized: bool = True) -> dict:
        headers = {"User-Agent": self.user_agent}
        if authorized:
            headers["Authorization"] = f"Bearer {self.get_token()}"
        return headers

    def get_token(self) -> str:
        """Fetch and cache an OAuth2 client-credentials token, refreshing
        shortly before it expires. Sends the required User-Agent header on
        the token request itself, not just subsequent API calls -- Halo's
        2026 AWS/EKS migration makes this mandatory (see 4.8)."""
        if self._token and time.time() < self._token_expires_at:
            return self._token

        logger.debug("fetching Halo token from %s", self.auth_url)
        response = self._client.post(
            self.auth_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": "all",
            },
            headers={"User-Agent": self.user_agent},
        )
        response.raise_for_status()
        payload = response.json()
        self._token = payload["access_token"]
        expires_in = payload.get("expires_in", 3600)
        self._token_expires_at = time.time() + expires_in - TOKEN_EXPIRY_MARGIN_SECONDS
        return self._token

    def list_assets_page(self, page_no: int, page_size: int = DEFAULT_PAGE_SIZE) -> dict:
        """One page of GET /api/Asset with includeassetfields=true (needed
        to get the `fields` array back at all -- omitted by default).
        Returns the raw decoded JSON: {page_no, page_size, record_count,
        assets}."""
        response = self._client.get(
            f"{self.base_url}/api/Asset",
            params={
                "pageinate": "true",
                "page_no": page_no,
                "page_size": page_size,
                "includeassetfields": "true",
            },
            headers=self._headers(),
        )
        response.raise_for_status()
        return response.json()

    def iter_assets(self, page_size: int = DEFAULT_PAGE_SIZE) -> Iterator[dict]:
        """Yield every raw asset dict in the tenant, paginating as needed.

        No server-side filter-by-field-value exists for Assets (unlike
        Tickets' view_id) -- confirmed against the live tenant's Swagger.
        `advanced_search` accepts a JSON array parameter but silently
        ignores keys it doesn't recognize rather than erroring, so it
        could not be reverse-engineered without Halo's own source; a Halo
        Report was tried as an alternative and proved impractical to
        configure. Callers filter client-side (see asset_source.py) --
        deferred as a future optimization once the qty field is rolled out
        tenant-wide and the asset count makes the full-list cost real
        rather than hypothetical.

        Tracks cumulative fetched count against `record_count` rather than
        `page_no * page_size`, since the live tenant silently caps the
        effective page size (confirmed: requesting 200 returns page_size
        50 in the response) -- multiplying by the *requested* size
        undercounts how many pages are actually needed and stops early.
        """
        page_no = 1
        fetched = 0
        while True:
            page = self.list_assets_page(page_no, page_size)
            assets = page.get("assets", [])
            yield from assets
            fetched += len(assets)
            record_count = page.get("record_count", fetched)
            if not assets or fetched >= record_count:
                return
            page_no += 1

    def claim(self, asset_id: int, qty_field_id: int) -> None:
        """Write qty_field_id's value back to 0. Called immediately after
        reading an asset off the pending list, before printing (see the
        project spec's poll-loop design, section 4.4). This is the only
        write-back the service ever makes to an asset -- no separate
        "printed" stamp (see 3.1)."""
        response = self._client.post(
            f"{self.base_url}/api/Asset",
            json=[{"id": asset_id, "fields": [{"id": qty_field_id, "value": "0"}]}],
            headers=self._headers(),
        )
        response.raise_for_status()
