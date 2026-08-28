"""LLM-based gene extraction — caching dispatcher over provider backends."""

import logging

from pipeline.config import PipelineConfig
from pipeline.llm_providers import (
    ExtractionFailedError,
    GeneEntry,
    LLMProvider,
    get_provider,
)
from pipeline.quality_metrics import TokenUsage
from pipeline.rate_limiter import AsyncRateLimiter

logger = logging.getLogger(__name__)

__all__ = [
    "ExtractionFailedError",
    "GeneEntry",
    "close_async_client",
    "extract_from_paper",
]

_provider: LLMProvider | None = None
_provider_name: str | None = None


async def extract_from_paper(
    text: str,
    pmid: str,
    config: PipelineConfig | None = None,
    rate_limiter: AsyncRateLimiter | None = None,
) -> tuple[list[GeneEntry], TokenUsage]:
    """Extract genes from paper text using the configured LLM provider.

    Caches one provider instance; when `config.llm_provider` changes between
    calls, closes the previous provider and builds a fresh one.
    """
    global _provider, _provider_name
    if not text or not text.strip():
        logger.warning("Empty text provided for PMID %s", pmid)
        return [], TokenUsage()

    config = config or PipelineConfig()
    if _provider is None or _provider_name != config.llm_provider:
        if _provider is not None:
            await _provider.close()
        _provider = get_provider(config)
        _provider_name = config.llm_provider
    return await _provider.extract(text, pmid, config, rate_limiter)


async def close_async_client() -> None:
    """Close the cached provider. Idempotent: safe before any init."""
    global _provider, _provider_name
    if _provider is not None:
        await _provider.close()
    _provider = None
    _provider_name = None
