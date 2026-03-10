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
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Final

import httpx

from pipeline.cache_utils import DEFAULT_EVICT_FRACTION, DEFAULT_MAX_SIZE, evict_lru
from pipeline.config import (
    NCBI_ESEARCH_URL,
    NCBI_ESUMMARY_URL,
    VALID_GWAS_TRAITS,
    PipelineConfig,
    get_ncbi_params,
)
from pipeline.http_client import AsyncHttpClientManager
from pipeline.llm_extraction import GeneEntry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------

# With API key: 10 req/s → 0.1s minimum interval
# Without API key: 3 req/s → 0.34s minimum interval
_MIN_REQUEST_INTERVAL: Final[float] = 0.1 if os.getenv("NCBI_API_KEY") else 0.34

_last_request_time: float = 0.0
_throttle_lock: asyncio.Lock | None = None
_validation_state_initialized: bool = False


def init_validation_state(config: PipelineConfig | None = None) -> None:
    """Eagerly initialize module-level locks and semaphores.

    Must be called once from the running event loop before concurrent use.
    Safe to call multiple times (idempotent).
    """
    global _throttle_lock, _cache_lock, _ncbi_semaphore, _validation_state_initialized
    if _validation_state_initialized:
        return
    if _throttle_lock is None:
        _throttle_lock = asyncio.Lock()
    if _cache_lock is None:
        _cache_lock = asyncio.Lock()
    if _ncbi_semaphore is None:
        limit = config.ncbi_rate_limit if config else PipelineConfig().ncbi_rate_limit
        _ncbi_semaphore = asyncio.Semaphore(limit)
    _validation_state_initialized = True


def _get_throttle_lock() -> asyncio.Lock:
    """Get throttle lock, initializing lazily if needed."""
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


# ---------------------------------------------------------------------------
# HTTP CLIENT AND CACHE
# ---------------------------------------------------------------------------

_client_manager = AsyncHttpClientManager(timeout=15.0)


async def close_validation_client() -> None:
    """Close shared HTTP client (call at shutdown)."""
    await _client_manager.close()


_gene_cache: OrderedDict[str, dict[str, Any] | None] = OrderedDict()
_cache_lock: asyncio.Lock | None = None
_ncbi_semaphore: asyncio.Semaphore | None = None


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


def clear_gene_cache() -> None:
    """Clear the gene validation cache."""
    global _gene_cache
    _gene_cache = OrderedDict()


# ---------------------------------------------------------------------------
# NCBI API HELPERS
# ---------------------------------------------------------------------------


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

    full_params = get_ncbi_params(params)
    client = await _client_manager.get()

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


# ---------------------------------------------------------------------------
# VALIDATION
# ---------------------------------------------------------------------------


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
            _gene_cache.move_to_end(symbol_upper)
            return _gene_cache[symbol_upper]

    # Semaphore controls concurrent NCBI requests; throttle enforces inter-request delay
    async with _get_ncbi_semaphore(config):
        result = await _fetch_ncbi_gene_uncached(symbol, config=config)

    async with _get_cache_lock():
        evict_lru(
            _gene_cache,
            DEFAULT_MAX_SIZE,
            DEFAULT_EVICT_FRACTION,
            "gene validation cache",
        )
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
    url = NCBI_ESEARCH_URL
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
    url = NCBI_ESUMMARY_URL
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

        symbol = gene_data.get("name", "")
        if not symbol:
            logger.warning(
                f"NCBI gene_id {gene_id} has no symbol "
                f"(name field missing or empty)"
            )
            return None

        return {
            "gene_id": gene_id,
            "symbol": symbol,
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
