"""LLM-based gene extraction using Claude API with streaming.

Extracts genes with putative causal links to cSVD from research papers
using the Anthropic Claude API, with structured output validation.

Uses the streaming API (required for adaptive thinking on Opus 4.6 when
requests may exceed 10 minutes) with structured outputs (constrained
decoding) for guaranteed valid JSON — replacing the previous manual JSON
schema prompting approach.

Features:
- Streaming API for long-running adaptive thinking requests
- Structured outputs via output_config for guaranteed valid JSON
- Prompt caching (system + static instructions cached across calls)
- Token-bucket rate limiting (proactive, not reactive)
- Separate retry budgets for rate-limit vs validation errors
- Token usage tracking for cost visibility
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import anthropic
import httpx
from anthropic import transform_schema
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pipeline.config import PipelineConfig
from pipeline.prompts import build_extraction_messages
from pipeline.quality_metrics import TokenUsage, accumulate_usage
from pipeline.rate_limiter import AsyncRateLimiter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MODELS
# ---------------------------------------------------------------------------


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
    """Wrapper model for structured extraction."""

    genes: list[GeneEntry] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# OUTPUT CONFIGURATION
# ---------------------------------------------------------------------------

# Pre-computed structured output config (schema cached by API for 24h after first use).
_OUTPUT_CONFIG: dict[str, Any] = {
    "format": {
        "type": "json_schema",
        "schema": transform_schema(ExtractionResult),
    }
}


# ---------------------------------------------------------------------------
# CLIENT MANAGEMENT
# ---------------------------------------------------------------------------

_async_client: anthropic.AsyncAnthropic | None = None


def _get_async_client() -> anthropic.AsyncAnthropic:
    """Get or create shared async Anthropic client.

    Returns the raw AsyncAnthropic client (not Instructor-wrapped) to
    support streaming, which is required for adaptive thinking requests
    that may exceed 10 minutes.
    """
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


def _parse_extraction_response(text: str) -> ExtractionResult:
    """Parse structured output JSON into ExtractionResult.

    With structured outputs (constrained decoding), the response is
    guaranteed valid JSON matching the schema. Only Pydantic validation
    (e.g. confidence range) can fail.

    Raises:
        json.JSONDecodeError: If response is not valid JSON (shouldn't happen).
        ValidationError: If JSON doesn't satisfy Pydantic constraints.
    """
    data = json.loads(text.strip())
    return ExtractionResult.model_validate(data)


# ---------------------------------------------------------------------------
# EXTRACTION
# ---------------------------------------------------------------------------


async def extract_from_paper(
    text: str,
    pmid: str,
    config: PipelineConfig | None = None,
    rate_limiter: AsyncRateLimiter | None = None,
) -> tuple[list[GeneEntry], TokenUsage]:
    """Extract genes using Claude API with streaming and Pydantic validation.

    Uses the Anthropic streaming API (required for adaptive thinking on
    Opus 4.6 when requests may exceed 10 minutes) with JSON schema
    prompting and Pydantic validation.

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
        prompt_version=config.prompt_version,
    )

    rate_limit_retries = 0
    validation_retries = 0
    connection_retries = 0

    while True:
        try:
            # Proactive rate limiting
            if rate_limiter is not None:
                await rate_limiter.acquire(
                    estimated_tokens=config.estimated_tokens_per_call
                )

            # Build streaming kwargs — adaptive thinking dynamically
            # allocates reasoning depth per request.
            output_config = dict(_OUTPUT_CONFIG)
            if config.llm_effort != "high":
                # "high" is the API default — only send when overridden
                output_config["effort"] = config.llm_effort

            stream_kwargs: dict[str, Any] = {
                "model": config.llm_model,
                "max_tokens": config.llm_max_tokens,
                "system": system_blocks,
                "messages": messages,
                "thinking": {"type": "adaptive"},
                "output_config": output_config,
            }

            stream_start = time.monotonic()
            async with client.messages.stream(**stream_kwargs) as stream:
                response = await stream.get_final_message()
            stream_elapsed = time.monotonic() - stream_start

            # Track token usage from final message
            accumulate_usage(usage, response)

            # Correct rate limiter estimate with actual usage
            if (
                rate_limiter is not None
                and hasattr(response, "usage")
                and response.usage
            ):
                actual = response.usage.input_tokens + response.usage.output_tokens
                await rate_limiter.record_actual_usage(
                    config.estimated_tokens_per_call, actual
                )

            # Detect truncation: with adaptive thinking, max_tokens covers
            # both thinking + text output. If thinking consumed most of the
            # budget, the JSON output gets cut off mid-stream.
            if response.stop_reason == "max_tokens":
                used = response.usage.output_tokens if response.usage else "?"
                raise ValueError(
                    f"Response truncated (stop_reason=max_tokens, "
                    f"output_tokens={used}/{config.llm_max_tokens}). "
                    f"Increase PIPELINE_LLM_MAX_TOKENS or "
                    f"reduce effort level."
                )

            # Extract text content and estimate thinking tokens from
            # content blocks. The API lumps thinking + text into
            # output_tokens, so we estimate the split from char counts.
            text_content = ""
            thinking_chars = 0
            text_chars = 0
            for block in response.content:
                block_type = getattr(block, "type", None)
                if block_type == "thinking":
                    thinking_chars += len(getattr(block, "thinking", ""))
                elif block_type == "text":
                    block_text: str = getattr(block, "text", "")
                    text_content += block_text
                    text_chars += len(block_text)

            # Estimate thinking tokens from character ratio
            total_chars = thinking_chars + text_chars
            if total_chars > 0 and usage.output_tokens > 0:
                thinking_ratio = thinking_chars / total_chars
                usage.thinking_tokens = int(usage.output_tokens * thinking_ratio)

            # Log timing and token breakdown for observability
            tok_per_sec = (
                usage.output_tokens / stream_elapsed if stream_elapsed > 0 else 0
            )
            logger.info(
                f"  LLM stream: {stream_elapsed:.1f}s, "
                f"{usage.output_tokens:,} output tokens "
                f"(~{usage.thinking_tokens:,} thinking + "
                f"~{usage.text_output_tokens:,} text), "
                f"{tok_per_sec:.0f} tok/s"
            )

            if not text_content.strip():
                logger.warning(f"Empty text response for PMID {pmid}")
                return [], usage

            # Parse and validate
            result = _parse_extraction_response(text_content)
            logger.info(f"Extracted {len(result.genes)} gene(s) from PMID {pmid}")
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
                f"Rate limited on PMID {pmid}. "
                f"Waiting {delay:.1f}s ({delay_source}) "
                f"(rate limit retry "
                f"{rate_limit_retries}/{config.max_rate_limit_retries})..."
            )
            if rate_limiter is not None:
                rate_limiter.signal_rate_limit(delay)
            await asyncio.sleep(delay)

        except (
            anthropic.APIConnectionError,
            httpx.RemoteProtocolError,
            httpx.ReadError,
            httpx.ConnectError,
        ) as e:
            connection_retries += 1
            if connection_retries > config.max_connection_retries:
                logger.error(
                    f"Connection retries exhausted for PMID {pmid} "
                    f"({connection_retries}/{config.max_connection_retries}): {e}"
                )
                return [], usage
            backoff_delay = min(
                config.connection_retry_delay * (2 ** (connection_retries - 1)),
                64.0,
            )
            logger.warning(
                f"Connection error on PMID {pmid}: {e!r}. "
                f"Retrying in {backoff_delay:.1f}s "
                f"(connection retry "
                f"{connection_retries}/{config.max_connection_retries})..."
            )
            await asyncio.sleep(backoff_delay)

        except (json.JSONDecodeError, ValidationError, ValueError) as e:
            validation_retries += 1
            if validation_retries > config.max_retries:
                logger.error(
                    f"Validation retries exhausted for PMID {pmid} "
                    f"({validation_retries}/{config.max_retries}): {e}"
                )
                return [], usage
            logger.warning(
                f"Validation retry {validation_retries}/{config.max_retries} "
                f"for PMID {pmid}: {e}"
            )

        except anthropic.APIError as e:
            logger.error(f"Claude API error for PMID {pmid}: {e}")
            return [], usage

        except Exception as e:
            logger.error(f"Extraction failed for PMID {pmid}: {e}")
            return [], usage
