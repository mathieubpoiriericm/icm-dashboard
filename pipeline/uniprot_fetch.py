"""UniProt protein information fetching module.

Fetches protein data (accession, GO annotations, protein name) from UniProt
and stores results in PostgreSQL for dashboard consumption.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Final

import httpx

logger = logging.getLogger(__name__)

# Rate limiting for UniProt
UNIPROT_RATE_LIMIT: Final[int] = 5
UNIPROT_BASE_URL: Final[str] = "https://rest.uniprot.org/uniprotkb/search"


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


@dataclass(slots=True)
class SyncResult:
    """Result of sync operation."""

    fetched: int
    cached: int
    failed: int
    errors: list[str]


# Module-level shared HTTP client
_http_client: httpx.AsyncClient | None = None
_uniprot_cache: dict[str, UniProtInfo | None] = {}
_cache_lock = asyncio.Lock()
_uniprot_semaphore = asyncio.Semaphore(UNIPROT_RATE_LIMIT)


async def _get_http_client() -> httpx.AsyncClient:
    """Get or create shared HTTP client with connection pooling."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=30.0,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
    return _http_client


async def close_uniprot_client() -> None:
    """Close shared HTTP client (call at shutdown)."""
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


def clear_uniprot_cache() -> None:
    """Clear the UniProt info cache."""
    global _uniprot_cache
    _uniprot_cache = {}


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


async def fetch_uniprot_accession(gene_symbol: str) -> tuple[str | None, str | None]:
    """Fetch UniProt accession for a gene symbol.

    Args:
        gene_symbol: Gene symbol to look up.

    Returns:
        Tuple of (accession, protein_name) or (None, None) if not found.
    """
    # Try exact gene name match first
    params = {
        "query": f'gene_exact:"{gene_symbol}" AND organism_id:9606',
        "format": "tsv",
        "fields": "accession,gene_primary,gene_synonym,protein_name",
        "size": "5",
    }

    try:
        client = await _get_http_client()
        resp = await client.get(UNIPROT_BASE_URL, params=params)

        if resp.status_code != 200:
            logger.warning(f"UniProt search failed for {gene_symbol}: {resp.status_code}")
            return None, None

        lines = resp.text.strip().split("\n")
        if len(lines) < 2:
            # No results with exact match, try synonym search
            params["query"] = f'gene:"{gene_symbol}" AND organism_id:9606'
            resp = await client.get(UNIPROT_BASE_URL, params=params)

            if resp.status_code != 200:
                return None, None

            lines = resp.text.strip().split("\n")
            if len(lines) < 2:
                logger.debug(f"No UniProt entry found for {gene_symbol}")
                return None, None

        # Parse TSV response - first line is header
        # Prefer exact primary gene match, otherwise take first result
        header = lines[0].split("\t")
        accession_idx = header.index("Entry") if "Entry" in header else 0
        gene_idx = header.index("Gene Names (primary)") if "Gene Names (primary)" in header else 1
        protein_idx = header.index("Protein names") if "Protein names" in header else 3

        best_accession = None
        best_protein = None

        for line in lines[1:]:
            cols = line.split("\t")
            if len(cols) > max(accession_idx, gene_idx, protein_idx):
                accession = cols[accession_idx]
                primary_gene = cols[gene_idx] if gene_idx < len(cols) else ""
                protein_name = cols[protein_idx] if protein_idx < len(cols) else ""

                # Prefer exact match on primary gene
                if primary_gene.upper() == gene_symbol.upper():
                    return accession, protein_name

                # Store first result as fallback
                if best_accession is None:
                    best_accession = accession
                    best_protein = protein_name

        return best_accession, best_protein

    except httpx.TimeoutException:
        logger.warning(f"Timeout querying UniProt for {gene_symbol}")
    except httpx.RequestError as e:
        logger.warning(f"Request error querying UniProt for {gene_symbol}: {e}")
    except (ValueError, IndexError) as e:
        logger.warning(f"Failed to parse UniProt response for {gene_symbol}: {e}")

    return None, None


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
        client = await _get_http_client()
        resp = await client.get(url, params=params)

        if resp.status_code != 200:
            logger.warning(f"UniProt GO fetch failed for {accession}: {resp.status_code}")
            return {
                "biological_process": None,
                "molecular_function": None,
                "cellular_component": None,
            }

        lines = resp.text.strip().split("\n")
        if len(lines) < 2:
            return {
                "biological_process": None,
                "molecular_function": None,
                "cellular_component": None,
            }

        # Parse TSV - first line is header, second is data
        cols = lines[1].split("\t")

        return {
            "biological_process": _clean_go_term(cols[0] if len(cols) > 0 else None),
            "molecular_function": _clean_go_term(cols[1] if len(cols) > 1 else None),
            "cellular_component": _clean_go_term(cols[2] if len(cols) > 2 else None),
        }

    except httpx.TimeoutException:
        logger.warning(f"Timeout fetching GO info for {accession}")
    except httpx.RequestError as e:
        logger.warning(f"Request error fetching GO info for {accession}: {e}")
    except (ValueError, IndexError) as e:
        logger.warning(f"Failed to parse GO response for {accession}: {e}")

    return {
        "biological_process": None,
        "molecular_function": None,
        "cellular_component": None,
    }


async def fetch_uniprot_info(gene_symbol: str) -> UniProtInfo | None:
    """Fetch complete UniProt information for a gene symbol.

    Results are cached to avoid redundant API calls.

    Args:
        gene_symbol: Gene symbol to look up.

    Returns:
        UniProtInfo if found, None otherwise.
    """
    symbol_upper = gene_symbol.upper()

    # Check cache first (without lock for read)
    if symbol_upper in _uniprot_cache:
        return _uniprot_cache[symbol_upper]

    async with _cache_lock:
        # Double-check after acquiring lock
        if symbol_upper in _uniprot_cache:
            return _uniprot_cache[symbol_upper]

        async with _uniprot_semaphore:
            result = await _fetch_uniprot_uncached(gene_symbol)
            _uniprot_cache[symbol_upper] = result
            return result


async def _fetch_uniprot_uncached(gene_symbol: str) -> UniProtInfo:
    """Internal: fetch UniProt data without caching."""
    accession, protein_name = await fetch_uniprot_accession(gene_symbol)

    if not accession:
        return UniProtInfo(
            gene_symbol=gene_symbol,
            accession=None,
            protein_name=None,
            biological_process=None,
            molecular_function=None,
            cellular_component=None,
            url=None,
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
) -> list[UniProtInfo]:
    """Fetch UniProt info for multiple genes.

    Args:
        gene_symbols: List of gene symbols to fetch.
        progress_callback: Optional callback(current, total) for progress updates.

    Returns:
        List of UniProtInfo objects.
    """
    results: list[UniProtInfo] = []
    total = len(gene_symbols)

    for i, symbol in enumerate(gene_symbols):
        info = await fetch_uniprot_info(symbol)
        if info is not None:
            results.append(info)
        else:
            results.append(
                UniProtInfo(
                    gene_symbol=symbol,
                    accession=None,
                    protein_name=None,
                    biological_process=None,
                    molecular_function=None,
                    cellular_component=None,
                    url=None,
                )
            )

        if progress_callback:
            progress_callback(i + 1, total)

    return results


async def sync_uniprot_info(gene_symbols: list[str]) -> SyncResult:
    """Sync UniProt info to database for given gene symbols.

    Args:
        gene_symbols: List of gene symbols to sync.

    Returns:
        SyncResult with counts of fetched, cached, and failed genes.
    """
    from pipeline.database import get_cached_uniprot_info, upsert_uniprot_batch

    # Check what's already cached in database
    cached_genes = await get_cached_uniprot_info(gene_symbols)
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
    def log_progress(current: int, total: int) -> None:
        if current % 10 == 0 or current == total:
            logger.info(f"  UniProt fetch progress: {current}/{total}")

    fetched_genes = await fetch_uniprot_batch(symbols_to_fetch, log_progress)

    # Store in database
    successful = [g for g in fetched_genes if g.accession is not None]
    failed = [g for g in fetched_genes if g.accession is None]

    if successful:
        await upsert_uniprot_batch(successful)

    # Also store failed lookups so we don't retry them
    if failed:
        await upsert_uniprot_batch(failed)

    return SyncResult(
        fetched=len(successful),
        cached=len(cached_genes),
        failed=len(failed),
        errors=[f"UniProt not found: {g.gene_symbol}" for g in failed],
    )
