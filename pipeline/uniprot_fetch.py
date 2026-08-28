"""UniProt protein information fetching module.

Fetches protein data (accession, GO annotations, protein name) from UniProt
and stores results in PostgreSQL for dashboard consumption.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Final

import httpx

from pipeline.cache_utils import (
    DB_CACHE_TTL_DAYS,
    SyncResult,
    make_log_progress,
    run_batched_fetch,
    single_flight_get,
)
from pipeline.config import PipelineConfig
from pipeline.http_client import AsyncHttpClientManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------

UNIPROT_BASE_URL: Final[str] = "https://rest.uniprot.org/uniprotkb/search"
_GO_FIELDS: Final[tuple[str, str, str]] = (
    "biological_process",
    "molecular_function",
    "cellular_component",
)


def _empty_go_info() -> dict[str, str | None]:
    """Return an empty GO-annotation mapping."""
    return dict.fromkeys(_GO_FIELDS)


@dataclass(slots=True)
class UniProtInfo:
    """UniProt protein information for a single gene."""

    gene_symbol: str
    accession: str | None
    protein_name: str | None
    biological_process: str | None
    molecular_function: str | None
    cellular_component: str | None
    url: str | None
    cacheable_miss: bool = True


# ---------------------------------------------------------------------------
# HTTP CLIENT AND CACHE
# ---------------------------------------------------------------------------

# Module-level shared HTTP client (30s timeout for UniProt's slower API)
_client_manager = AsyncHttpClientManager(timeout=30.0)
_uniprot_cache: OrderedDict[str, UniProtInfo | None] = OrderedDict()
_cache_lock: asyncio.Lock | None = None
_uniprot_semaphore: asyncio.Semaphore | None = None
# Single-flight registry keyed by uppercase symbol; see ``single_flight_get``.
_in_flight: dict[str, asyncio.Task[UniProtInfo | None]] = {}


def _get_cache_lock() -> asyncio.Lock:
    """Lazy-init cache lock (avoids creating Lock before event loop exists)."""
    global _cache_lock
    if _cache_lock is None:
        _cache_lock = asyncio.Lock()
    return _cache_lock


def _get_uniprot_semaphore(config: PipelineConfig | None = None) -> asyncio.Semaphore:
    """Get or create the UniProt rate-limit semaphore."""
    global _uniprot_semaphore
    if _uniprot_semaphore is None:
        limit = (
            config.uniprot_rate_limit if config else PipelineConfig().uniprot_rate_limit
        )
        _uniprot_semaphore = asyncio.Semaphore(limit)
    return _uniprot_semaphore


async def close_uniprot_client() -> None:
    """Close shared HTTP client (call at shutdown)."""
    await _client_manager.close()


def clear_uniprot_cache() -> None:
    """Clear the UniProt info cache and any in-flight task references."""
    global _uniprot_cache
    _uniprot_cache = OrderedDict()
    _in_flight.clear()


def _clean_go_term(text: str | None) -> str | None:
    """Clean GO annotation text by removing GO IDs in brackets.

    Example: "apoptotic process [GO:0006915]" -> "apoptotic process"
    """
    if not text:
        return None

    # Remove GO IDs like [GO:0006915]
    cleaned = re.sub(r"\s*\[GO:\d+\]", "", text)
    # Clean up multiple semicolons and whitespace
    cleaned = re.sub(r";\s*;", ";", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip() or None


def _parse_search_rows(
    lines: list[str], gene_symbol: str
) -> tuple[str | None, str | None]:
    """Select an exact primary-gene match, falling back to the first row."""
    header = lines[0].split("\t")

    def _column_index(name: str, fallback: int) -> int:
        return header.index(name) if name in header else fallback

    accession_index = _column_index("Entry", 0)
    gene_index = _column_index("Gene Names (primary)", 1)
    protein_index = _column_index("Protein names", 3)
    required_columns = max(accession_index, gene_index, protein_index)
    fallback: tuple[str, str] | None = None

    for line in lines[1:]:
        columns = line.split("\t")
        if len(columns) <= required_columns:
            continue
        accession = columns[accession_index]
        primary_gene = columns[gene_index]
        protein_name = columns[protein_index]
        if primary_gene.upper() == gene_symbol.upper():
            return accession, protein_name
        if fallback is None:
            fallback = accession, protein_name

    return fallback or (None, None)


# ---------------------------------------------------------------------------
# SEARCH FUNCTIONS
# ---------------------------------------------------------------------------


async def _fetch_uniprot_accession_status(
    gene_symbol: str,
) -> tuple[str | None, str | None, bool]:
    """Fetch UniProt accession for a gene symbol.

    Args:
        gene_symbol: Gene symbol to look up.

    Returns:
        Tuple of (accession, protein_name, cacheable_miss). ``cacheable_miss``
        is False for transient API/client failures that should not be stored
        as durable negative cache rows.
    """
    params = {
        "format": "tsv",
        "fields": "accession,gene_primary,gene_synonym,protein_name",
        "size": "5",
    }

    try:
        client = await _client_manager.get()
        queries = (
            f'gene_exact:"{gene_symbol}" AND organism_id:9606',
            f'gene:"{gene_symbol}" AND organism_id:9606',
        )
        for query in queries:
            params["query"] = query
            resp = await client.get(UNIPROT_BASE_URL, params=params)
            if resp.status_code != 200:
                logger.warning(
                    f"UniProt search failed for {gene_symbol}: {resp.status_code}"
                )
                return None, None, False
            lines = resp.text.strip().split("\n")
            if len(lines) >= 2:
                accession, protein_name = _parse_search_rows(lines, gene_symbol)
                return accession, protein_name, True

        logger.debug(f"No UniProt entry found for {gene_symbol}")
        return None, None, True

    except httpx.TimeoutException:
        logger.warning(f"Timeout querying UniProt for {gene_symbol}")
    except httpx.RequestError as e:
        logger.warning(f"Request error querying UniProt for {gene_symbol}: {e}")
    except (ValueError, IndexError) as e:
        logger.warning(f"Failed to parse UniProt response for {gene_symbol}: {e}")

    return None, None, False


async def fetch_uniprot_accession(gene_symbol: str) -> tuple[str | None, str | None]:
    """Fetch UniProt accession for a gene symbol.

    Args:
        gene_symbol: Gene symbol to look up.

    Returns:
        Tuple of (accession, protein_name) or (None, None) if not found or if
        UniProt is temporarily unavailable.
    """
    accession, protein_name, _ = await _fetch_uniprot_accession_status(gene_symbol)
    return accession, protein_name


async def fetch_uniprot_go_info(accession: str) -> dict[str, str | None]:
    """Fetch GO annotations for a UniProt accession.

    Args:
        accession: UniProt accession ID.

    Returns:
        Dict with biological_process, molecular_function, cellular_component.
    """
    url = f"https://rest.uniprot.org/uniprotkb/{accession}"
    params = {
        "format": "tsv",
        "fields": "go_p,go_f,go_c",
    }

    try:
        client = await _client_manager.get()
        resp = await client.get(url, params=params)

        if resp.status_code != 200:
            logger.warning(
                f"UniProt GO fetch failed for {accession}: {resp.status_code}"
            )
            return _empty_go_info()

        lines = resp.text.strip().split("\n")
        if len(lines) < 2:
            return _empty_go_info()

        # Parse TSV - first line is header, second is data
        cols = lines[1].split("\t")

        return {
            field: _clean_go_term(cols[index] if index < len(cols) else None)
            for index, field in enumerate(_GO_FIELDS)
        }

    except httpx.TimeoutException:
        logger.warning(f"Timeout fetching GO info for {accession}")
    except httpx.RequestError as e:
        logger.warning(f"Request error fetching GO info for {accession}: {e}")
    except (ValueError, IndexError) as e:
        logger.warning(f"Failed to parse GO response for {accession}: {e}")

    return _empty_go_info()


async def fetch_uniprot_info(
    gene_symbol: str,
    config: PipelineConfig | None = None,
) -> UniProtInfo | None:
    """Fetch complete UniProt information for a gene symbol.

    Results are cached; concurrent callers for the same symbol share one
    in-flight fetch via ``single_flight_get``.
    """
    return await single_flight_get(
        gene_symbol.upper(),
        cache=_uniprot_cache,
        cache_lock=_get_cache_lock(),
        in_flight=_in_flight,
        semaphore=_get_uniprot_semaphore(config),
        fetch_fn=lambda: _fetch_uniprot_uncached(gene_symbol),
        label="UniProt cache",
    )


async def _fetch_uniprot_uncached(gene_symbol: str) -> UniProtInfo:
    """Internal: fetch UniProt data without caching."""
    accession, protein_name, cacheable_miss = await _fetch_uniprot_accession_status(
        gene_symbol
    )

    if not accession:
        return UniProtInfo(
            gene_symbol=gene_symbol,
            accession=None,
            protein_name=None,
            biological_process=None,
            molecular_function=None,
            cellular_component=None,
            url=None,
            cacheable_miss=cacheable_miss,
        )

    # Fetch GO annotations
    go_info = await fetch_uniprot_go_info(accession)

    return UniProtInfo(
        gene_symbol=gene_symbol,
        accession=accession,
        protein_name=protein_name,
        biological_process=go_info["biological_process"],
        molecular_function=go_info["molecular_function"],
        cellular_component=go_info["cellular_component"],
        url=f"https://www.uniprot.org/uniprotkb/{accession}/entry",
    )


async def fetch_uniprot_batch(
    gene_symbols: list[str],
    progress_callback: Any | None = None,
    config: PipelineConfig | None = None,
) -> list[UniProtInfo]:
    """Fetch UniProt info for multiple genes concurrently.

    Uses the module-level semaphore (via fetch_uniprot_info) to rate-limit
    concurrent requests.
    """

    async def _fetch_one(symbol: str) -> UniProtInfo:
        info = await fetch_uniprot_info(symbol, config=config)
        return info or UniProtInfo(
            gene_symbol=symbol,
            accession=None,
            protein_name=None,
            biological_process=None,
            molecular_function=None,
            cellular_component=None,
            url=None,
        )

    return await run_batched_fetch(
        gene_symbols, _fetch_one, progress_callback=progress_callback
    )


# ---------------------------------------------------------------------------
# DATABASE SYNC
# ---------------------------------------------------------------------------


async def sync_uniprot_info(
    gene_symbols: list[str],
    config: PipelineConfig | None = None,
) -> SyncResult:
    """Sync UniProt info to database for given gene symbols.

    Args:
        gene_symbols: List of gene symbols to sync.
        config: Pipeline config for UniProt semaphore sizing.

    Returns:
        SyncResult with counts of fetched, cached, and failed genes.
    """
    from pipeline.database import get_cached_uniprot_info, upsert_uniprot_batch

    # Fresh rows only — stale rows fall through to a re-fetch.
    cached_genes = await get_cached_uniprot_info(
        gene_symbols, max_age_days=DB_CACHE_TTL_DAYS
    )
    symbols_to_fetch = [s for s in gene_symbols if s not in cached_genes]

    logger.info(
        f"UniProt sync: {len(cached_genes)} cached, {len(symbols_to_fetch)} to fetch"
    )

    if not symbols_to_fetch:
        return SyncResult(
            fetched=0,
            cached=len(cached_genes),
            failed=0,
            errors=[],
        )

    # Fetch missing genes
    fetched_genes = await fetch_uniprot_batch(
        symbols_to_fetch,
        make_log_progress("UniProt fetch"),
        config=config,
    )

    # Store in database
    successful = [g for g in fetched_genes if g.accession is not None]
    failed = [g for g in fetched_genes if g.accession is None]
    cacheable_failed = [g for g in failed if g.cacheable_miss]

    if successful:
        await upsert_uniprot_batch(successful)

    # Store confirmed "not found" lookups, but do not persist transient API
    # failures as 30-day negative cache rows.
    if cacheable_failed:
        await upsert_uniprot_batch(cacheable_failed)

    return SyncResult(
        fetched=len(successful),
        cached=len(cached_genes),
        failed=len(failed),
        errors=[f"UniProt not found: {g.gene_symbol}" for g in failed],
    )
