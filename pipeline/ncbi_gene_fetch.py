"""NCBI Gene information fetching module.

Fetches gene metadata (uid, description, aliases) from NCBI Gene database
and stores results in PostgreSQL for dashboard consumption.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import httpx

from pipeline.cache_utils import (
    DEFAULT_EVICT_FRACTION,
    DEFAULT_MAX_SIZE,
    SyncResult,
    evict_lru,
    make_log_progress,
)
from pipeline.config import PipelineConfig
from pipeline.http_client import AsyncHttpClientManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MODELS
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class NCBIGeneInfo:
    """NCBI Gene information for a single gene."""

    gene_symbol: str
    ncbi_uid: str | None
    description: str | None
    aliases: str | None


# ---------------------------------------------------------------------------
# HTTP CLIENT AND CACHE
# ---------------------------------------------------------------------------

# Module-level shared HTTP client
_client_manager = AsyncHttpClientManager(timeout=15.0)
_gene_cache: OrderedDict[str, NCBIGeneInfo | None] = OrderedDict()
_cache_lock: asyncio.Lock | None = None
_ncbi_semaphore: asyncio.Semaphore | None = None
_ncbi_fetch_state_initialized: bool = False


def init_ncbi_fetch_state(config: PipelineConfig | None = None) -> None:
    """Eagerly initialize module-level locks and semaphores.

    Must be called once from the running event loop before concurrent use.
    Safe to call multiple times (idempotent).
    """
    global _cache_lock, _ncbi_semaphore, _ncbi_fetch_state_initialized
    if _ncbi_fetch_state_initialized:
        return
    if _cache_lock is None:
        _cache_lock = asyncio.Lock()
    if _ncbi_semaphore is None:
        limit = config.ncbi_rate_limit if config else PipelineConfig().ncbi_rate_limit
        _ncbi_semaphore = asyncio.Semaphore(limit)
    _ncbi_fetch_state_initialized = True


def _get_cache_lock() -> asyncio.Lock:
    """Get cache lock, initializing lazily if needed."""
    global _cache_lock
    if _cache_lock is None:
        _cache_lock = asyncio.Lock()
    return _cache_lock


def _get_ncbi_semaphore(config: PipelineConfig | None = None) -> asyncio.Semaphore:
    """Get NCBI rate-limit semaphore, initializing lazily if needed."""
    global _ncbi_semaphore
    if _ncbi_semaphore is None:
        limit = config.ncbi_rate_limit if config else PipelineConfig().ncbi_rate_limit
        _ncbi_semaphore = asyncio.Semaphore(limit)
    return _ncbi_semaphore


async def close_ncbi_client() -> None:
    """Close shared HTTP client (call at shutdown)."""
    await _client_manager.close()


def clear_ncbi_cache() -> None:
    """Clear the gene info cache."""
    global _gene_cache
    _gene_cache = OrderedDict()


# ---------------------------------------------------------------------------
# FETCH FUNCTIONS
# ---------------------------------------------------------------------------


async def fetch_ncbi_gene_info(gene_symbol: str) -> NCBIGeneInfo | None:
    """Fetch NCBI gene information for a single gene symbol.

    Results are cached to avoid redundant API calls.

    Args:
        gene_symbol: Gene symbol to look up.

    Returns:
        NCBIGeneInfo if found, None otherwise.
    """
    symbol_upper = gene_symbol.upper()

    # Check cache first (without lock for read — LRU recency not updated,
    # acceptable tradeoff to avoid lock contention on every read)
    if symbol_upper in _gene_cache:
        return _gene_cache[symbol_upper]

    async with _get_cache_lock():
        # Double-check after acquiring lock
        if symbol_upper in _gene_cache:
            _gene_cache.move_to_end(symbol_upper)
            return _gene_cache[symbol_upper]

    # Lock released before I/O
    async with _get_ncbi_semaphore():
        result = await _fetch_ncbi_gene_uncached(gene_symbol)

    async with _get_cache_lock():
        evict_lru(
            _gene_cache, DEFAULT_MAX_SIZE, DEFAULT_EVICT_FRACTION, "NCBI gene cache"
        )
        _gene_cache[symbol_upper] = result
    return result


async def _fetch_ncbi_gene_uncached(gene_symbol: str) -> NCBIGeneInfo | None:
    """Internal: fetch gene from NCBI without caching."""
    # Step 1: Search for gene ID
    search_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    search_params = {
        "db": "gene",
        "term": f"{gene_symbol}[Gene Name] AND Homo sapiens[Organism]",
        "retmode": "json",
    }

    try:
        client = await _client_manager.get()
        resp = await client.get(search_url, params=search_params)

        if resp.status_code != 200:
            logger.warning(f"NCBI esearch failed for {gene_symbol}: {resp.status_code}")
            return None

        data = resp.json()
        if data["esearchresult"]["count"] == "0":
            logger.debug(f"Gene {gene_symbol} not found in NCBI")
            return NCBIGeneInfo(
                gene_symbol=gene_symbol,
                ncbi_uid=None,
                description=None,
                aliases=None,
            )

        gene_id = data["esearchresult"]["idlist"][0]

        # Step 2: Get gene summary
        return await _fetch_gene_summary(gene_symbol, gene_id)

    except httpx.TimeoutException:
        logger.warning(f"Timeout querying NCBI for gene {gene_symbol}")
    except httpx.RequestError as e:
        logger.warning(f"Request error querying NCBI for gene {gene_symbol}: {e}")
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        logger.warning(f"Unexpected NCBI response format for gene {gene_symbol}: {e}")

    return None


async def _fetch_gene_summary(gene_symbol: str, gene_id: str) -> NCBIGeneInfo | None:
    """Fetch gene summary details from NCBI esummary."""
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
    params = {"db": "gene", "id": gene_id, "retmode": "json"}

    try:
        client = await _client_manager.get()
        resp = await client.get(url, params=params)

        if resp.status_code != 200:
            logger.warning(
                f"NCBI esummary failed for gene_id {gene_id}: {resp.status_code}"
            )
            return None

        data = resp.json()
        result = data.get("result", {})
        gene_data = result.get(gene_id, {})

        if not gene_data or "error" in gene_data:
            return None

        return NCBIGeneInfo(
            gene_symbol=gene_symbol,
            ncbi_uid=gene_id,
            description=gene_data.get("description", ""),
            aliases=gene_data.get("otheraliases", ""),
        )

    except httpx.TimeoutException:
        logger.warning(f"Timeout fetching gene summary for gene_id {gene_id}")
    except httpx.RequestError as e:
        logger.warning(
            f"Request error fetching gene summary for gene_id {gene_id}: {e}"
        )
    except (KeyError, ValueError, json.JSONDecodeError) as e:
        logger.warning(f"Failed to parse NCBI response for gene_id {gene_id}: {e}")

    return None


async def fetch_ncbi_genes_batch(
    gene_symbols: list[str],
    progress_callback: Any | None = None,
) -> list[NCBIGeneInfo]:
    """Fetch NCBI gene info for multiple genes concurrently.

    Uses the module-level semaphore (via fetch_ncbi_gene_info) to
    rate-limit concurrent requests.

    Args:
        gene_symbols: List of gene symbols to fetch.
        progress_callback: Optional callback(current, total) for progress updates.

    Returns:
        List of NCBIGeneInfo objects (some may have None fields if lookup failed).
    """
    total = len(gene_symbols)
    completed = 0
    completed_lock = asyncio.Lock()

    async def _fetch_one(symbol: str) -> NCBIGeneInfo:
        nonlocal completed
        info = await fetch_ncbi_gene_info(symbol)
        result = (
            info
            if info is not None
            else NCBIGeneInfo(
                gene_symbol=symbol,
                ncbi_uid=None,
                description=None,
                aliases=None,
            )
        )
        async with completed_lock:
            completed += 1
            if progress_callback:
                progress_callback(completed, total)
        return result

    async with asyncio.TaskGroup() as tg:
        tasks = [tg.create_task(_fetch_one(s)) for s in gene_symbols]

    return [t.result() for t in tasks]


# ---------------------------------------------------------------------------
# DATABASE SYNC
# ---------------------------------------------------------------------------


async def sync_ncbi_gene_info(gene_symbols: list[str]) -> SyncResult:
    """Sync NCBI gene info to database for given gene symbols.

    Args:
        gene_symbols: List of gene symbols to sync.

    Returns:
        SyncResult with counts of fetched, cached, and failed genes.
    """
    from pipeline.database import get_cached_ncbi_genes, upsert_ncbi_genes_batch

    # Check what's already cached in database
    cached_genes = await get_cached_ncbi_genes(gene_symbols)
    symbols_to_fetch = [s for s in gene_symbols if s not in cached_genes]

    logger.info(
        f"NCBI sync: {len(cached_genes)} cached, {len(symbols_to_fetch)} to fetch"
    )

    if not symbols_to_fetch:
        return SyncResult(
            fetched=0,
            cached=len(cached_genes),
            failed=0,
            errors=[],
        )

    # Fetch missing genes
    fetched_genes = await fetch_ncbi_genes_batch(
        symbols_to_fetch, make_log_progress("NCBI fetch")
    )

    # Store in database
    successful = [g for g in fetched_genes if g.ncbi_uid is not None]
    failed = [g for g in fetched_genes if g.ncbi_uid is None]

    if successful:
        await upsert_ncbi_genes_batch(successful)

    # Also store failed lookups so we don't retry them
    if failed:
        await upsert_ncbi_genes_batch(failed)

    return SyncResult(
        fetched=len(successful),
        cached=len(cached_genes),
        failed=len(failed),
        errors=[f"Gene not found: {g.gene_symbol}" for g in failed],
    )
