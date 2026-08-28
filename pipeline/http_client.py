"""Shared async HTTP client manager for external API modules.

Encapsulates the get-or-create / close / reset pattern used by
ncbi_gene_fetch, uniprot_fetch, pubmed_citations, validation,
and pdf_retrieval.
"""

import asyncio
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
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        # Lazy so the lock binds to whichever event loop is actually running
        # when the first caller arrives (tests re-use the manager across loops).
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def get(self) -> httpx.AsyncClient:
        """Get or create the shared HTTP client."""
        if self._client is not None:
            return self._client
        async with self._get_lock():
            if self._client is None:
                self._client = httpx.AsyncClient(
                    timeout=self._timeout,
                    limits=self._limits,
                    **self._client_kwargs,
                )
            return self._client

    async def close(self) -> None:
        """Close the HTTP client (call at shutdown)."""
        async with self._get_lock():
            if self._client is not None:
                await self._client.aclose()
                self._client = None

    def reset(self) -> None:
        """Reset client reference without closing (for test teardown)."""
        self._client = None
        self._lock = None
