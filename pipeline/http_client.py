"""Shared async HTTP client manager for external API modules.

Encapsulates the get-or-create / close / reset pattern used by
ncbi_gene_fetch, uniprot_fetch, and pubmed_citations.
"""

from __future__ import annotations

import httpx


class AsyncHttpClientManager:
    """Lazy singleton manager for an httpx.AsyncClient."""

    def __init__(
        self,
        timeout: float = 15.0,
        limits: httpx.Limits | None = None,
    ) -> None:
        self._client: httpx.AsyncClient | None = None
        self._timeout = timeout
        self._limits = limits or httpx.Limits(
            max_connections=10, max_keepalive_connections=5
        )

    async def get(self) -> httpx.AsyncClient:
        """Get or create the shared HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                limits=self._limits,
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client (call at shutdown)."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def reset(self) -> None:
        """Reset client reference without closing (for test teardown)."""
        self._client = None
