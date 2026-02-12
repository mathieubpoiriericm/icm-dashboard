"""LLM-based gene extraction using Claude API with Instructor.

Extracts genes with putative causal links to cSVD from research papers
using the Anthropic Claude API, with Pydantic-validated structured output
via Instructor.

Features:
- Instructor-enforced Pydantic schema for structured extraction
- Adaptive thinking (Claude dynamically allocates reasoning depth)
- Prompt caching (system + static instructions cached across calls)
- Token-bucket rate limiting (proactive, not reactive)
- Separate retry budgets for rate-limit vs validation errors
- Token usage tracking for cost visibility
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import anthropic
import instructor
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
    pmid: str = ""


class ExtractionResult(BaseModel):
    """Wrapper model for Instructor structured extraction."""

    genes: list[GeneEntry] = Field(default_factory=list)


# --- Async client singleton (Instructor-wrapped) ---
_async_client: Any = None


def _get_async_client() -> Any:
    """Get or create shared Instructor-wrapped async Anthropic client.

    Uses ANTHROPIC_JSON mode (not ANTHROPIC_TOOLS) because the default
    tool mode sets ``tool_choice`` to force a specific tool, which the
    Anthropic API rejects when thinking is enabled.  JSON mode embeds
    the schema in the prompt instead and parses the text response.
    """
    global _async_client
    if _async_client is None:
        _async_client = instructor.from_anthropic(
            anthropic.AsyncAnthropic(),
            mode=instructor.Mode.ANTHROPIC_JSON,
        )
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
) -> tuple[list[GeneEntry], TokenUsage]:
    """Extract genes using Claude API with Instructor for structured output.

    Uses Instructor to enforce the ExtractionResult Pydantic schema as the
    response format, with auto-retry on validation failure. Prompt caching
    and proactive rate limiting are preserved.

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

    rate_limit_retries = 0

    while True:
        try:
            # Proactive rate limiting
            if rate_limiter is not None:
                await rate_limiter.acquire(
                    estimated_tokens=config.estimated_tokens_per_call
                )

            # Build API call kwargs — Instructor handles schema enforcement.
            # Adaptive thinking lets Claude dynamically allocate reasoning
            # depth per request; effort controls the baseline reasoning level.
            create_kwargs: dict[str, Any] = {
                "model": config.llm_model,
                "max_tokens": config.llm_max_tokens,
                "system": system_blocks,
                "messages": messages,
                "response_model": ExtractionResult,
                "max_retries": config.max_retries,
                "thinking": {"type": "adaptive"},
            }
            if config.llm_effort != "high":
                # "high" is the API default — only send when overridden
                create_kwargs["output"] = {"effort": config.llm_effort}

            result, completion = await client.messages.create_with_completion(
                **create_kwargs
            )

            # Track token usage from raw completion
            accumulate_usage(usage, completion)

            # Correct rate limiter estimate with actual usage
            if rate_limiter is not None and hasattr(completion, "usage") and completion.usage:
                actual = completion.usage.input_tokens + completion.usage.output_tokens
                await rate_limiter.record_actual_usage(
                    config.estimated_tokens_per_call, actual
                )

            return result.genes, usage

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

        except anthropic.APIError as e:
            logger.error(f"Claude API error for PMID {pmid}: {e}")
            return [], usage

        except Exception as e:
            # Catches Instructor validation exhaustion and unexpected errors
            logger.error(f"Extraction failed for PMID {pmid}: {e}")
            return [], usage


# ---------------------------------------------------------------------------
# DEPRECATED: parse_llm_response
# Kept for one run cycle as a safety net. Will be removed after the
# Instructor integration is verified in production.
# ---------------------------------------------------------------------------


def parse_llm_response(text: str) -> list[dict[str, Any]] | None:
    """Parse the LLM response JSON into gene entries.

    .. deprecated::
        Superseded by Instructor structured extraction. Kept temporarily
        as a rollback safety net.
    """
    if not text or not text.strip():
        return None

    json_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if json_match:
        text = json_match.group(1)

    try:
        parsed = json.loads(text.strip())
        if isinstance(parsed, dict):
            return parsed.get("genes", [])
    except json.JSONDecodeError:
        pass

    json_obj_match = re.search(r"\{[\s\S]*\}", text)
    if json_obj_match:
        try:
            parsed = json.loads(json_obj_match.group())
            if isinstance(parsed, dict):
                return parsed.get("genes", [])
        except json.JSONDecodeError:
            pass

    genes_match = re.search(r'"genes"\s*:\s*(\[[\s\S]*?\])', text)
    if genes_match:
        try:
            return json.loads(genes_match.group(1))
        except json.JSONDecodeError:
            pass

    logger.warning("Failed to parse LLM response as JSON")
    return None
