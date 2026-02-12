"""LLM-based gene extraction using Claude API.

Extracts genes with putative causal links to cSVD from research papers
using the Anthropic Claude API.

Features:
- Prompt caching (system + static instructions cached across calls)
- Token-bucket rate limiting (proactive, not reactive)
- Separate retry budgets for rate-limit vs parse/API errors
- Token usage tracking for cost visibility
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import anthropic
from pydantic import BaseModel, ConfigDict, Field

from pipeline.config import PipelineConfig
from pipeline.prompts import build_extraction_messages
from pipeline.quality_metrics import TokenUsage, accumulate_usage
from pipeline.rate_limiter import AsyncRateLimiter

logger = logging.getLogger(__name__)


class GeneEntry(BaseModel):
    """Extracted gene entry from paper analysis."""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_default=True,
    )

    gene_symbol: str
    protein_name: str | None = None
    gwas_trait: list[str] = Field(default_factory=list)
    mendelian_randomization: bool = False
    omics_evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    causal_evidence_summary: str | None = None


# --- Async client singleton ---
_async_client: anthropic.AsyncAnthropic | None = None


def _get_async_client() -> anthropic.AsyncAnthropic:
    """Get or create shared async Anthropic client."""
    global _async_client
    if _async_client is None:
        _async_client = anthropic.AsyncAnthropic()
    return _async_client


def _parse_retry_after_delay(
    e: anthropic.RateLimitError, backoff_delay: float
) -> tuple[float, str]:
    """Parse the retry-after header from a 429 response, falling back to backoff."""
    retry_after = e.response.headers.get("retry-after") if e.response else None
    if retry_after:
        try:
            return min(float(retry_after), 64.0), f"retry-after={retry_after}s"
        except ValueError:
            return backoff_delay, "backoff (retry-after parse failed)"
    return backoff_delay, "backoff"


async def extract_from_paper(
    text: str,
    pmid: str,
    config: PipelineConfig | None = None,
    rate_limiter: AsyncRateLimiter | None = None,
) -> tuple[list[dict[str, Any]], TokenUsage]:
    """Extract genes using Claude API with rate limiting and retry handling.

    Uses prompt caching (system prompt + instructions are cached), separate
    retry budgets for rate-limit vs parse/API errors, and proactive rate
    limiting via the token-bucket ``rate_limiter``.

    Args:
        text: Full text content of the paper.
        pmid: PubMed ID for context.
        config: Pipeline configuration (uses defaults if None).
        rate_limiter: Optional rate limiter for coordinated throttling.

    Returns:
        Tuple of (gene_entries, token_usage).
    """
    if config is None:
        config = PipelineConfig()

    usage = TokenUsage()

    if not text or not text.strip():
        logger.warning(f"Empty text provided for PMID {pmid}")
        return [], usage

    client = _get_async_client()

    # Build cached prompt structure
    system_blocks, messages = build_extraction_messages(
        paper_text=text,
        pmid=pmid,
        max_chars=config.max_paper_text_chars,
    )

    attempt = 0
    rate_limit_retries = 0

    while attempt < config.max_retries:
        attempt += 1
        try:
            # Proactive rate limiting
            if rate_limiter is not None:
                await rate_limiter.acquire(
                    estimated_tokens=config.estimated_tokens_per_call
                )

            response = await client.messages.create(
                model=config.llm_model,
                max_tokens=config.llm_max_tokens,
                temperature=config.llm_temperature,
                system=system_blocks,
                messages=messages,
            )

            # Track token usage
            accumulate_usage(usage, response)

            # Correct rate limiter estimate with actual usage
            if rate_limiter is not None and hasattr(response, "usage") and response.usage:
                actual = response.usage.input_tokens + response.usage.output_tokens
                await rate_limiter.record_actual_usage(
                    config.estimated_tokens_per_call, actual
                )

            if not response.content:
                logger.warning(f"Empty response from Claude for PMID {pmid}")
                return [], usage

            first_block = response.content[0]
            if not hasattr(first_block, "text"):
                logger.warning(f"No text in response for PMID {pmid}")
                continue

            parsed = parse_llm_response(first_block.text)
            if parsed is None:
                logger.warning(
                    f"Failed to parse JSON for PMID {pmid} (attempt {attempt})"
                )
                continue

            return parsed, usage

        except anthropic.RateLimitError as e:
            rate_limit_retries += 1
            if rate_limit_retries > config.max_rate_limit_retries:
                logger.error(
                    f"Rate limit retries exhausted for PMID {pmid} "
                    f"({rate_limit_retries}/{config.max_rate_limit_retries})"
                )
                return [], usage

            backoff_delay = min(
                config.rate_limit_retry_delay * (2 ** (rate_limit_retries - 1)), 64.0
            )
            delay, delay_source = _parse_retry_after_delay(e, backoff_delay)
            logger.warning(
                f"Rate limited on PMID {pmid}. Waiting {delay:.1f}s ({delay_source}) "
                f"(rate limit retry {rate_limit_retries}/{config.max_rate_limit_retries})..."
            )
            if rate_limiter is not None:
                rate_limiter.signal_rate_limit(delay)
            await asyncio.sleep(delay)
            # Don't consume parse/validation retry budget on rate limits
            attempt -= 1

        except anthropic.APIError as e:
            logger.error(f"Claude API error for PMID {pmid}: {e}")
            if attempt == config.max_retries:
                return [], usage
            delay = config.retry_delay * (2 ** (attempt - 1))
            await asyncio.sleep(delay)

    return [], usage


def parse_llm_response(text: str) -> list[dict[str, Any]] | None:
    """Parse the LLM response JSON into gene entries.

    Handles various response formats:
    - Direct JSON object
    - JSON wrapped in markdown code blocks
    - Partial/malformed JSON with recovery

    Args:
        text: Raw text response from LLM.

    Returns:
        List of gene entries, or None if parsing fails entirely.
    """
    if not text or not text.strip():
        return None

    # 4-tier fallback strategy for parsing LLM JSON output

    # Tier 1: Extract JSON from markdown code blocks (```json ... ```)
    json_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if json_match:
        text = json_match.group(1)

    # Tier 2: Direct JSON parsing (cleanest case)
    try:
        parsed = json.loads(text.strip())
        if isinstance(parsed, dict):
            return parsed.get("genes", [])
    except json.JSONDecodeError:
        pass

    # Tier 3: Find JSON object anywhere in text (handles preamble/postamble)
    json_obj_match = re.search(r"\{[\s\S]*\}", text)
    if json_obj_match:
        try:
            parsed = json.loads(json_obj_match.group())
            if isinstance(parsed, dict):
                return parsed.get("genes", [])
        except json.JSONDecodeError:
            pass

    # Tier 4: Extract genes array directly (handles malformed outer object)
    genes_match = re.search(r'"genes"\s*:\s*(\[[\s\S]*?\])', text)
    if genes_match:
        try:
            return json.loads(genes_match.group(1))
        except json.JSONDecodeError:
            pass

    logger.warning("Failed to parse LLM response as JSON")
    return None
