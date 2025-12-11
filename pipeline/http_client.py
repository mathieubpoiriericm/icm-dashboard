"""Shared async HTTP client manager for external API modules.

Encapsulates the get-or-create / close / reset pattern used by
ncbi_gene_fetch, uniprot_fetch, pubmed_citations, validation,
and pdf_retrieval.
"""

from __future__ import annotations

from typing import Any

import httpx


class AsyncHttpClientManager:
    """Lazy singleton manager for an httpx.AsyncClient."""

    def __init__(
        self,
        timeout: float | httpx.Timeout = 15.0,
        limits: httpx.Limits | None = None,
        **client_kwargs: Any,
    ) -> None:
        self._client: httpx.AsyncClient | None = None
        self._timeout = timeout
        self._limits = limits or httpx.Limits(
            max_connections=10, max_keepalive_connections=5
        )
        self._client_kwargs = client_kwargs

    async def get(self) -> httpx.AsyncClient:
        """Get or create the shared HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                limits=self._limits,
                **self._client_kwargs,
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
