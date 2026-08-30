"""HTTP-klient mot Kivras GraphQL-BFF och dokument-API."""

from __future__ import annotations

import time
from typing import Any, Iterator

import httpx

from .. import config
from .auth import Token
from .queries import RECEIPT_DETAILS_QUERY, RECEIPTS_QUERY

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
)


class KivraError(RuntimeError):
    """Kivra svarade med fel."""


class KivraClient:
    def __init__(self, token: Token, delay: float | None = None) -> None:
        self._token = token
        self._delay = config.REQUEST_DELAY_SECONDS if delay is None else delay
        self._client = httpx.Client(timeout=60)

    def __enter__(self) -> "KivraClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Origin": "https://inbox.kivra.com",
            "Referer": "https://inbox.kivra.com/",
            "Authorization": f"Bearer {self._token.access_token}",
            "X-Actor-Key": self._token.actor_key,
            "X-Actor-Type": "user",
            "X-Session-Actor": f"user_{self._token.actor_key}",
            "X-Kivra-Environment": "production",
            "User-Agent": USER_AGENT,
            "Accept-Language": "sv",
        }

    def graphql(self, operation: str, query: str, variables: dict[str, Any]) -> dict:
        response = self._client.post(
            config.KIVRA_BFF_URL,
            json={"operationName": operation, "query": query, "variables": variables},
            headers=self._headers,
        )
        if response.status_code == 401:
            raise KivraError("Token avvisad (401). Kör `icakort auth` igen.")
        if response.status_code != 200:
            raise KivraError(f"{operation} gav HTTP {response.status_code}: {response.text[:300]}")
        payload = response.json()
        if payload.get("errors"):
            raise KivraError(f"{operation} gav GraphQL-fel: {payload['errors']}")
        return payload["data"]

    def iter_receipts(self, page_size: int | None = None) -> Iterator[dict]:
        """Bläddra igenom kvittolistan, nyaste först."""
        limit = page_size or config.RECEIPT_PAGE_SIZE
        offset = 0
        while True:
            data = self.graphql(
                "Receipts",
                RECEIPTS_QUERY,
                {"search": None, "limit": limit, "offset": offset},
            )
            page = data["receiptsV2"]
            items = page.get("list") or []
            for item in items:
                yield item
            offset += len(items)
            if not items or offset >= page.get("total", 0):
                return
            time.sleep(self._delay)

    def receipt_details(self, key: str) -> dict:
        data = self.graphql("ReceiptDetails", RECEIPT_DETAILS_QUERY, {"key": key})
        receipt = data.get("receiptV2")
        if receipt is None:
            raise KivraError(f"Kvitto {key} saknar detaljer")
        time.sleep(self._delay)
        return receipt

    def receipt_pdf(self, key: str) -> bytes:
        url = f"{config.KIVRA_API_BASE}/v1/user/{self._token.actor_key}/receipts/{key}"
        response = self._client.get(
            url,
            headers={
                "Authorization": f"token {self._token.access_token}",
                "Accept": "application/pdf",
                "User-Agent": USER_AGENT,
            },
        )
        if response.status_code != 200:
            raise KivraError(f"PDF för {key} gav HTTP {response.status_code}")
        return response.content
