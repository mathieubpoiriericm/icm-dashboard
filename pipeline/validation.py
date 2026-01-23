"""Gene validation module with NCBI caching.

Validates extracted genes against NCBI Gene database with caching
to avoid redundant API calls for repeated gene symbols.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Final

import httpx

logger = logging.getLogger(__name__)

# --- Constants ---
CONFIDENCE_THRESHOLD: Final[float] = 0.7

# Valid GWAS traits from cSVD literature
VALID_GWAS_TRAITS: Final[frozenset[str]] = frozenset({
    "WMH",       # White matter hyperintensities
    "SVS",       # Small vessel stroke
    "BG-PVS",    # Basal ganglia perivascular spaces
    "WM-PVS",    # White matter perivascular spaces
    "HIP-PVS",   # Hippocampal perivascular spaces
    "PSMD",      # Peak width of skeletonized mean diffusivity
    "extreme-cSVD",
    "FA",        # Fractional anisotropy
    "lacunes",
    "stroke",
})

# Rate limiting for NCBI (3 req/sec without API key, 10 with)
NCBI_RATE_LIMIT: Final[int] = 10


@dataclass(slots=True)
class ValidationResult:
    """Result of gene entry validation."""

    is_valid: bool
    errors: list[str]
    warnings: list[str]
    normalized_data: dict[str, Any] | None


# --- Module-level shared HTTP client ---
_http_client: httpx.AsyncClient | None = None


async def _get_http_client() -> httpx.AsyncClient:
    """Get or create shared HTTP client with connection pooling."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=15.0,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
    return _http_client


async def close_validation_client() -> None:
    """Close shared HTTP client (call at shutdown)."""
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


# --- Gene cache for NCBI lookups ---
_gene_cache: dict[str, dict[str, Any] | None] = {}
_cache_lock = asyncio.Lock()
_ncbi_semaphore = asyncio.Semaphore(NCBI_RATE_LIMIT)


def clear_gene_cache() -> None:
    """Clear the gene validation cache."""
    global _gene_cache
    _gene_cache = {}


async def validate_gene_entry(entry: dict[str, Any]) -> ValidationResult:
    """Multi-stage gene validation.

    Validation stages (fail-fast on critical errors):
    1. Required field validation - ensures entry has necessary fields
    2. Confidence threshold - reject low-confidence LLM extractions
    3. NCBI Gene lookup - verify gene exists in human genome
    4. GWAS trait check - warn on unrecognized phenotypes (non-blocking)

    Args:
        entry: Gene entry dictionary from LLM extraction.

    Returns:
        ValidationResult with validation status and normalized data.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # Stage 0: Required field validation
    if "gene_symbol" not in entry:
        errors.append("Missing required field: gene_symbol")
        return ValidationResult(False, errors, warnings, None)

    gene_symbol = entry["gene_symbol"]
    if not isinstance(gene_symbol, str) or not gene_symbol.strip():
        errors.append("gene_symbol must be a non-empty string")
        return ValidationResult(False, errors, warnings, None)

    # Stage 1: Confidence threshold - filters out LLM hallucinations
    confidence = entry.get("confidence", 0.0)
    if not isinstance(confidence, (int, float)):
        errors.append(f"Invalid confidence type: {type(confidence).__name__}")
        return ValidationResult(False, errors, warnings, None)

    if confidence < CONFIDENCE_THRESHOLD:
        errors.append(f"Low confidence: {confidence:.2f} < {CONFIDENCE_THRESHOLD}")
        return ValidationResult(False, errors, warnings, None)

    # Stage 2: NCBI Gene validation - ensures gene symbol is real
    ncbi_info = await verify_ncbi_gene(gene_symbol)

    if not ncbi_info:
        errors.append(f"Gene '{gene_symbol}' not found in NCBI Gene")
        return ValidationResult(False, errors, warnings, None)

    # Normalize gene symbol to official NCBI symbol (handles aliases)
    if ncbi_info["symbol"] != gene_symbol:
        warnings.append(f"Normalized '{gene_symbol}' -> '{ncbi_info['symbol']}'")
        entry["gene_symbol"] = ncbi_info["symbol"]

    # Stage 3: GWAS trait validation (warnings only - unknown traits allowed)
    if gwas_traits := entry.get("gwas_trait"):
        for trait in gwas_traits:
            if trait not in VALID_GWAS_TRAITS:
                warnings.append(f"Unknown GWAS trait: {trait}")

    return ValidationResult(True, errors, warnings, entry)


async def verify_ncbi_gene(symbol: str) -> dict[str, Any] | None:
    """Query NCBI Gene database to verify gene symbol.

    Results are cached to avoid redundant API calls for repeated gene symbols.

    Args:
        symbol: Gene symbol to verify.

    Returns:
        Gene info dict if found, None otherwise.
    """
    symbol_upper = symbol.upper()

    # Check cache first (without lock for read)
    if symbol_upper in _gene_cache:
        return _gene_cache[symbol_upper]

    async with _cache_lock:
        # Double-check after acquiring lock
        if symbol_upper in _gene_cache:
            return _gene_cache[symbol_upper]

        async with _ncbi_semaphore:  # Rate limiting
            result = await _fetch_ncbi_gene_uncached(symbol)
            _gene_cache[symbol_upper] = result
            return result


async def _fetch_ncbi_gene_uncached(symbol: str) -> dict[str, Any] | None:
    """Internal: fetch gene from NCBI without caching.

    Args:
        symbol: Gene symbol to look up.

    Returns:
        Gene info dict if found, None otherwise.
    """
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    params = {
        "db": "gene",
        "term": f"{symbol}[Gene Name] AND Homo sapiens[Organism]",
        "retmode": "json",
    }

    try:
        client = await _get_http_client()
        resp = await client.get(url, params=params)

        if resp.status_code != 200:
            logger.warning(f"NCBI esearch failed for {symbol}: {resp.status_code}")
            return None

        data = resp.json()
        if data["esearchresult"]["count"] != "0":
            gene_id = data["esearchresult"]["idlist"][0]
            return await fetch_gene_details(gene_id)

    except httpx.TimeoutException:
        logger.warning(f"Timeout querying NCBI for gene {symbol}")
    except httpx.RequestError as e:
        logger.warning(f"Request error querying NCBI for gene {symbol}: {e}")
    except (KeyError, IndexError) as e:
        logger.warning(f"Unexpected NCBI response format for gene {symbol}: {e}")

    return None


async def fetch_gene_details(gene_id: str) -> dict[str, Any] | None:
    """Fetch gene details from NCBI using esummary.

    Args:
        gene_id: NCBI Gene ID.

    Returns:
        Gene metadata dict if successful, None otherwise.
    """
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
    params = {"db": "gene", "id": gene_id, "retmode": "json"}

    try:
        client = await _get_http_client()
        resp = await client.get(url, params=params)

        if resp.status_code != 200:
            logger.warning(f"NCBI esummary failed for gene_id {gene_id}: {resp.status_code}")
            return None

        data = resp.json()
        result = data.get("result", {})
        gene_data = result.get(gene_id, {})

        if not gene_data or "error" in gene_data:
            return None

        return {
            "gene_id": gene_id,
            "symbol": gene_data.get("name", ""),
            "description": gene_data.get("description", ""),
            "chromosome": gene_data.get("chromosome", ""),
            "aliases": (
                gene_data.get("otheraliases", "").split(", ")
                if gene_data.get("otheraliases")
                else []
            ),
        }

    except httpx.TimeoutException:
        logger.warning(f"Timeout fetching gene details for gene_id {gene_id}")
    except httpx.RequestError as e:
        logger.warning(f"Request error fetching gene details for gene_id {gene_id}: {e}")
    except (KeyError, ValueError) as e:
        logger.warning(f"Failed to parse NCBI response for gene_id {gene_id}: {e}")

    return None
