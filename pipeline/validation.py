"""Gene validation module with NCBI caching.

Validates extracted genes against NCBI Gene database with caching
to avoid redundant API calls for repeated gene symbols.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Final

import httpx

from pipeline.config import VALID_GWAS_TRAITS, PipelineConfig
from pipeline.llm_extraction import GeneEntry

logger = logging.getLogger(__name__)

# --- NCBI API key and throttling ---
NCBI_API_KEY: Final[str | None] = os.getenv("NCBI_API_KEY")

# With API key: 10 req/s → 0.1s minimum interval
# Without API key: 3 req/s → 0.34s minimum interval
_MIN_REQUEST_INTERVAL: Final[float] = 0.1 if NCBI_API_KEY else 0.34

_last_request_time: float = 0.0
_throttle_lock: asyncio.Lock | None = None


def _get_throttle_lock() -> asyncio.Lock:
    """Lazy-init throttle lock (avoids creating Lock before event loop exists)."""
    global _throttle_lock
    if _throttle_lock is None:
        _throttle_lock = asyncio.Lock()
    return _throttle_lock


@dataclass(slots=True)
class ValidationResult:
    """Result of gene entry validation."""

    is_valid: bool
    errors: list[str]
    warnings: list[str]
    normalized_data: GeneEntry | None


# --- Module-level shared HTTP client ---
_http_client: httpx.AsyncClient | None = None


async def _get_http_client() -> httpx.AsyncClient:
    """Get or create shared HTTP client with connection pooling."""
    global _http_client
    if _http_client is None:
        # 15s timeout: NCBI esearch responses are small JSON; longer
        # would mask network issues without improving availability.
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
_cache_lock: asyncio.Lock | None = None
_ncbi_semaphore: asyncio.Semaphore | None = None


def _get_cache_lock() -> asyncio.Lock:
    """Lazy-init cache lock (avoids creating Lock before event loop exists)."""
    global _cache_lock
    if _cache_lock is None:
        _cache_lock = asyncio.Lock()
    return _cache_lock


def _get_ncbi_semaphore(config: PipelineConfig | None = None) -> asyncio.Semaphore:
    """Get or create the NCBI rate-limit semaphore."""
    global _ncbi_semaphore
    if _ncbi_semaphore is None:
        limit = config.ncbi_rate_limit if config else PipelineConfig().ncbi_rate_limit
        _ncbi_semaphore = asyncio.Semaphore(limit)
    return _ncbi_semaphore


def clear_gene_cache() -> None:
    """Clear the gene validation cache."""
    global _gene_cache
    _gene_cache = {}


def _get_ncbi_params(base_params: dict[str, str]) -> dict[str, str]:
    """Add NCBI API key to params if available."""
    if NCBI_API_KEY:
        return {**base_params, "api_key": NCBI_API_KEY}
    return base_params


async def _throttle() -> None:
    """Enforce minimum interval between NCBI requests."""
    global _last_request_time
    async with _get_throttle_lock():
        now = time.monotonic()
        elapsed = now - _last_request_time
        if elapsed < _MIN_REQUEST_INTERVAL:
            await asyncio.sleep(_MIN_REQUEST_INTERVAL - elapsed)
        _last_request_time = time.monotonic()


async def _ncbi_get_with_retry(
    url: str,
    params: dict[str, str],
    *,
    config: PipelineConfig | None = None,
    context: str = "",
) -> httpx.Response | None:
    """HTTP GET with automatic API key injection, throttle, and 429 retry.

    Args:
        url: NCBI E-utility URL.
        params: Query parameters (api_key added automatically).
        config: Pipeline config for retry settings.
        context: Description for log messages (e.g. "esearch for NOTCH3").

    Returns:
        httpx.Response on success, None on exhausted retries or error.
    """
    if config is None:
        config = PipelineConfig()

    full_params = _get_ncbi_params(params)
    client = await _get_http_client()

    for attempt in range(1, config.max_rate_limit_retries + 1):
        await _throttle()
        try:
            resp = await client.get(url, params=full_params)
        except httpx.TimeoutException:
            logger.warning(f"Timeout on NCBI request ({context})")
            return None
        except httpx.RequestError as e:
            logger.warning(f"Request error on NCBI request ({context}): {e}")
            return None

        if resp.status_code != 429:
            return resp

        # 429 — rate limited, retry with backoff
        if attempt >= config.max_rate_limit_retries:
            logger.warning(
                f"NCBI rate limit retries exhausted ({context}): "
                f"{attempt}/{config.max_rate_limit_retries}"
            )
            return None

        backoff = min(config.rate_limit_retry_delay * (2 ** (attempt - 1)), 64.0)
        retry_after = resp.headers.get("retry-after")
        if retry_after:
            try:
                delay = min(float(retry_after), 64.0)
                delay_source = f"retry-after={retry_after}s"
            except ValueError:
                delay = backoff
                delay_source = "backoff (retry-after parse failed)"
        else:
            delay = backoff
            delay_source = "backoff"

        logger.warning(
            f"NCBI 429 ({context}). Waiting {delay:.1f}s ({delay_source}) "
            f"(attempt {attempt}/{config.max_rate_limit_retries})..."
        )
        await asyncio.sleep(delay)

    return None


async def validate_gene_entry(
    entry: GeneEntry,
    config: PipelineConfig | None = None,
) -> ValidationResult:
    """Multi-stage gene validation.

    Validation stages (fail-fast on critical errors):
    1. Confidence threshold - reject low-confidence LLM extractions
    2. NCBI Gene lookup - verify gene exists in human genome
    3. GWAS trait check - warn on unrecognized phenotypes (non-blocking)

    Note: Required field validation (Stage 0 in prior versions) is now
    handled by Pydantic via Instructor at extraction time.

    Args:
        entry: Gene entry from LLM extraction (Pydantic-validated).
        config: Pipeline configuration (uses defaults if None).

    Returns:
        ValidationResult with validation status and normalized data.
    """
    if config is None:
        config = PipelineConfig()

    errors: list[str] = []
    warnings: list[str] = []

    # Stage 1: Confidence threshold - filters out LLM hallucinations
    if entry.confidence < config.confidence_threshold:
        errors.append(
            f"Low confidence: {entry.confidence:.2f} < {config.confidence_threshold}"
        )
        return ValidationResult(False, errors, warnings, None)

    # Stage 2: NCBI Gene validation - ensures gene symbol is real
    ncbi_info = await verify_ncbi_gene(entry.gene_symbol, config=config)

    if not ncbi_info:
        errors.append(f"Gene '{entry.gene_symbol}' not found in NCBI Gene")
        return ValidationResult(False, errors, warnings, None)

    # Normalize gene symbol to official NCBI symbol (handles aliases)
    if ncbi_info["symbol"] != entry.gene_symbol:
        warnings.append(f"Normalized '{entry.gene_symbol}' -> '{ncbi_info['symbol']}'")
        entry.gene_symbol = ncbi_info["symbol"]

    # Stage 3: GWAS trait validation (warnings only - unknown traits allowed)
    for trait in entry.gwas_trait:
        if trait not in VALID_GWAS_TRAITS:
            warnings.append(f"Unknown GWAS trait: {trait}")

    return ValidationResult(True, errors, warnings, entry)


async def verify_ncbi_gene(
    symbol: str, *, config: PipelineConfig | None = None
) -> dict[str, Any] | None:
    """Query NCBI Gene database to verify gene symbol.

    Results are cached to avoid redundant API calls for repeated gene symbols.

    Args:
        symbol: Gene symbol to verify.
        config: Pipeline configuration for retry settings.

    Returns:
        Gene info dict if found, None otherwise.
    """
    symbol_upper = symbol.upper()

    # Check cache (brief lock for dict access only)
    async with _get_cache_lock():
        if symbol_upper in _gene_cache:
            return _gene_cache[symbol_upper]

    # Semaphore controls concurrent NCBI requests; throttle enforces inter-request delay
    async with _get_ncbi_semaphore(config):
        result = await _fetch_ncbi_gene_uncached(symbol, config=config)

    async with _get_cache_lock():
        _gene_cache[symbol_upper] = result

    return result


async def _fetch_ncbi_gene_uncached(
    symbol: str, *, config: PipelineConfig | None = None
) -> dict[str, Any] | None:
    """Internal: fetch gene from NCBI without caching.

    Args:
        symbol: Gene symbol to look up.
        config: Pipeline configuration for retry settings.

    Returns:
        Gene info dict if found, None otherwise.
    """
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    params = {
        "db": "gene",
        "term": f"{symbol}[Gene Name] AND Homo sapiens[Organism]",
        "retmode": "json",
    }

    resp = await _ncbi_get_with_retry(
        url, params, config=config, context=f"esearch for {symbol}"
    )
    if resp is None or resp.status_code != 200:
        if resp is not None:
            logger.warning(f"NCBI esearch failed for {symbol}: {resp.status_code}")
        return None

    try:
        data = resp.json()
        if data["esearchresult"]["count"] != "0":
            gene_id = data["esearchresult"]["idlist"][0]
            return await fetch_gene_details(gene_id, config=config)
    except (KeyError, IndexError) as e:
        logger.warning(f"Unexpected NCBI response format for gene {symbol}: {e}")

    return None


async def fetch_gene_details(
    gene_id: str, *, config: PipelineConfig | None = None
) -> dict[str, Any] | None:
    """Fetch gene details from NCBI using esummary.

    Args:
        gene_id: NCBI Gene ID.
        config: Pipeline configuration for retry settings.

    Returns:
        Gene metadata dict if successful, None otherwise.
    """
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
    params = {"db": "gene", "id": gene_id, "retmode": "json"}

    resp = await _ncbi_get_with_retry(
        url, params, config=config, context=f"esummary for gene_id {gene_id}"
    )
    if resp is None or resp.status_code != 200:
        if resp is not None:
            logger.warning(
                f"NCBI esummary failed for gene_id {gene_id}: {resp.status_code}"
            )
        return None

    try:
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
    except (KeyError, json.JSONDecodeError) as e:
        logger.warning(f"Failed to parse NCBI response for gene_id {gene_id}: {e}")

    return None
