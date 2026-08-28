"""Anthropic Claude backend for gene extraction.

Uses the Anthropic streaming API with adaptive thinking, structured
outputs (JSON Schema constrained decoding), and prompt caching.
"""

import asyncio
import json
import logging
import os
import time
from typing import Any

import anthropic
import httpx
from anthropic import transform_schema
from pydantic import ValidationError

from pipeline.config import (
    ADAPTIVE_THINKING_MODELS,
    EFFORT_CAPABLE_MODELS,
    THINKING_OUTPUT_RESERVE,
    PipelineConfig,
)
from pipeline.llm_providers.base import (
    ExtractionFailedError,
    ExtractionResult,
    GeneEntry,
    parse_extraction_response,
)
from pipeline.prompts import build_extraction_prompt
from pipeline.quality_metrics import TokenUsage, accumulate_usage
from pipeline.rate_limiter import AsyncRateLimiter, compute_backoff, resolve_retry_delay

# Pricing per 1M tokens (input, output) — bump when a new Claude model
# ships or Anthropic changes published rates.
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-7": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}

# Prompt-caching multipliers applied to the base input price. The pipeline
# writes 1h TTL caches (see prompts.py), so writes cost 2x base.
_CACHE_WRITE_MULTIPLIER: float = 2.0  # 1h TTL
_CACHE_READ_MULTIPLIER: float = 0.1

logger = logging.getLogger(__name__)


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


def _extract_response_text(response: Any) -> tuple[str, int, int]:
    """Return text plus thinking/text character counts from content blocks."""
    text_parts: list[str] = []
    thinking_chars = 0
    text_chars = 0
    for block in response.content:
        match getattr(block, "type", None):
            case "thinking":
                thinking_chars += len(getattr(block, "thinking", ""))
            case "text":
                block_text: str = getattr(block, "text", "")
                text_parts.append(block_text)
                text_chars += len(block_text)
    return "".join(text_parts), thinking_chars, text_chars


async def _release_reservation(
    rate_limiter: AsyncRateLimiter | None,
    request_id: int | None,
) -> None:
    """Release a failed call's pre-reserved token budget, when present."""
    if rate_limiter is not None and request_id is not None:
        await rate_limiter.record_actual_usage(request_id, 0)


def _build_stream_kwargs(
    text: str,
    pmid: str,
    config: PipelineConfig,
) -> dict[str, Any]:
    """Build the Anthropic streaming request payload."""
    prompt = build_extraction_prompt(
        paper_text=text,
        pmid=pmid,
        max_chars=config.max_paper_text_chars,
        prompt_version=config.prompt_version,
    )
    if config.llm_model in ADAPTIVE_THINKING_MODELS:
        # Summaries keep thinking blocks populated for the ratio estimator.
        thinking: dict[str, Any] = {
            "type": "adaptive",
            "display": "summarized",
        }
    else:
        budget = max(
            config.llm_max_tokens - THINKING_OUTPUT_RESERVE,
            config.llm_max_tokens // 2,
        )
        thinking = {"type": "enabled", "budget_tokens": budget}

    # "high" is the API default — only transmit when overridden.
    output_config = dict(_OUTPUT_CONFIG)
    if config.llm_model in EFFORT_CAPABLE_MODELS and config.llm_effort != "high":
        output_config["effort"] = config.llm_effort

    return {
        "model": config.llm_model,
        "max_tokens": config.llm_max_tokens,
        "system": [
            {
                "type": "text",
                "text": prompt.system_prompt,
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            },
            {
                "type": "text",
                "text": prompt.extraction_instructions,
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            },
        ],
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": prompt.user_text}],
            }
        ],
        "thinking": thinking,
        "output_config": output_config,
    }


async def _stream_and_parse(
    client: anthropic.AsyncAnthropic,
    stream_kwargs: dict[str, Any],
    usage: TokenUsage,
    *,
    pmid: str,
    config: PipelineConfig,
    rate_limiter: AsyncRateLimiter | None,
    request_id: int | None,
) -> list[GeneEntry]:
    """Consume one streamed response, account for it, and parse its genes."""
    stream_start = time.monotonic()
    async with client.messages.stream(**stream_kwargs) as stream:
        response = await stream.get_final_message()
    stream_elapsed = time.monotonic() - stream_start

    accumulate_usage(usage, response)
    if rate_limiter is not None and request_id is not None and response.usage:
        actual = response.usage.input_tokens + response.usage.output_tokens
        await rate_limiter.record_actual_usage(request_id, actual)

    # Truncation is a deterministic token-budget problem, so retrying the
    # identical request cannot help.
    if response.stop_reason == "max_tokens":
        used = response.usage.output_tokens if response.usage else "?"
        logger.error(
            f"Response truncated for PMID {pmid} "
            f"(stop_reason=max_tokens, "
            f"output_tokens={used}/{config.llm_max_tokens}). "
            f"Raise PIPELINE_LLM_MAX_TOKENS or reduce effort level."
        )
        usage.truncated_responses = 1
        raise ExtractionFailedError(f"Response truncated for PMID {pmid}", usage)

    text_content, thinking_chars, text_chars = _extract_response_text(response)
    total_chars = thinking_chars + text_chars
    if total_chars > 0 and usage.output_tokens > 0:
        usage.thinking_tokens = int(usage.output_tokens * thinking_chars / total_chars)

    tokens_per_second = (
        usage.output_tokens / stream_elapsed if stream_elapsed > 0 else 0
    )
    logger.info(
        f"  LLM stream: {stream_elapsed:.1f}s, "
        f"{usage.output_tokens:,} output tokens "
        f"(~{usage.thinking_tokens:,} thinking + "
        f"~{usage.text_output_tokens:,} text), "
        f"{tokens_per_second:.0f} tok/s"
    )

    if not text_content.strip():
        logger.warning(f"Empty text response for PMID {pmid}")
        raise ExtractionFailedError(f"Empty text response for PMID {pmid}", usage)

    result = parse_extraction_response(text_content)
    # Ignore any PMID supplied by the model in favor of the caller's value.
    for gene in result.genes:
        gene.pmid = pmid
    logger.info(f"Extracted {len(result.genes)} gene(s) from PMID {pmid}")
    return result.genes


async def _retry_rate_limit(
    error: anthropic.RateLimitError,
    retry_count: int,
    *,
    pmid: str,
    config: PipelineConfig,
    usage: TokenUsage,
    rate_limiter: AsyncRateLimiter | None,
) -> int:
    """Back off after a rate limit, or raise when its retry budget is spent."""
    retry_count += 1
    if retry_count > config.max_rate_limit_retries:
        logger.error(
            f"Rate limit retries exhausted for PMID {pmid} "
            f"({retry_count}/{config.max_rate_limit_retries})"
        )
        raise ExtractionFailedError(
            f"Rate limit retries exhausted for PMID {pmid}", usage
        ) from error

    backoff_delay = compute_backoff(config.rate_limit_retry_delay, retry_count)
    retry_after = error.response.headers.get("retry-after") if error.response else None
    delay, delay_source = resolve_retry_delay(retry_after, backoff_delay)
    logger.warning(
        f"Rate limited on PMID {pmid}. "
        f"Waiting {delay:.1f}s ({delay_source}) "
        f"(rate limit retry {retry_count}/{config.max_rate_limit_retries})..."
    )
    if rate_limiter is not None:
        await rate_limiter.signal_rate_limit(delay)
    await asyncio.sleep(delay)
    return retry_count


async def _retry_connection(
    error: Exception,
    retry_count: int,
    *,
    pmid: str,
    config: PipelineConfig,
    usage: TokenUsage,
) -> int:
    """Back off after a connection failure, or raise when retries are spent."""
    retry_count += 1
    if retry_count > config.max_connection_retries:
        logger.error(
            f"Connection retries exhausted for PMID {pmid} "
            f"({retry_count}/{config.max_connection_retries}): {error}"
        )
        raise ExtractionFailedError(
            f"Connection retries exhausted for PMID {pmid}: {error}", usage
        ) from error

    delay = compute_backoff(config.connection_retry_delay, retry_count)
    logger.warning(
        f"Connection error on PMID {pmid}: {error!r}. "
        f"Retrying in {delay:.1f}s "
        f"(connection retry {retry_count}/{config.max_connection_retries})..."
    )
    await asyncio.sleep(delay)
    return retry_count


def _retry_validation(
    error: Exception,
    retry_count: int,
    *,
    pmid: str,
    config: PipelineConfig,
    usage: TokenUsage,
) -> int:
    """Record a schema retry, or raise when its retry budget is spent."""
    retry_count += 1
    if retry_count > config.max_retries:
        logger.error(
            f"Validation retries exhausted for PMID {pmid} "
            f"({retry_count}/{config.max_retries}): {error}"
        )
        raise ExtractionFailedError(
            f"Validation retries exhausted for PMID {pmid}: {error}", usage
        ) from error
    logger.warning(
        f"Validation retry {retry_count}/{config.max_retries} for PMID {pmid}: {error}"
    )
    return retry_count


# ---------------------------------------------------------------------------
# PROVIDER
# ---------------------------------------------------------------------------


class AnthropicProvider:
    """Streaming Claude provider (current default)."""

    def __init__(self) -> None:
        self._client: anthropic.AsyncAnthropic | None = None

    def _get_client(self) -> anthropic.AsyncAnthropic:
        """Lazily build the raw AsyncAnthropic client.

        Raw (not Instructor-wrapped) because we use the streaming API,
        which is required for adaptive-thinking requests that may exceed
        10 minutes of wall-clock time.
        """
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise ExtractionFailedError(
                "ANTHROPIC_API_KEY is required for the Anthropic provider. "
                "Set it in .env before running the pipeline."
            )
        if self._client is None:
            self._client = anthropic.AsyncAnthropic()
        return self._client

    async def close(self) -> None:
        """Close the underlying client. Idempotent."""
        if self._client is not None:
            await self._client.close()
            self._client = None

    def report_metadata(self, config: PipelineConfig) -> dict[str, Any]:
        return {
            "model": config.llm_model,
            "model_version": config.model_version,
            "thinking_mode": config.thinking_mode,
            "effort": config.llm_effort,
            "prompt_version": config.prompt_version,
        }

    def estimate_cost(self, usage: TokenUsage, config: PipelineConfig) -> float | None:
        pricing = _MODEL_PRICING.get(config.llm_model)
        if pricing is None:
            logger.warning(
                "No pricing data for model %s — cost will be omitted",
                config.llm_model,
            )
            return None
        input_price, output_price = pricing
        return (
            usage.input_tokens * input_price
            + usage.cache_creation_input_tokens * input_price * _CACHE_WRITE_MULTIPLIER
            + usage.cache_read_input_tokens * input_price * _CACHE_READ_MULTIPLIER
            + usage.output_tokens * output_price
        ) / 1_000_000

    async def extract(
        self,
        text: str,
        pmid: str,
        config: PipelineConfig,
        rate_limiter: AsyncRateLimiter | None,
    ) -> tuple[list[GeneEntry], TokenUsage]:
        """Extract genes using Claude API with streaming and Pydantic validation.

        Uses the Anthropic streaming API (required for adaptive thinking
        when requests may exceed 10 minutes) with JSON schema prompting
        and Pydantic validation.

        Args:
            text: Full text content of the paper.
            pmid: PubMed ID for context.
            config: Pipeline configuration.
            rate_limiter: Optional rate limiter for coordinated throttling.

        Returns:
            Tuple of (gene_entries, token_usage).
        """
        usage = TokenUsage()
        client = self._get_client()
        stream_kwargs = _build_stream_kwargs(text, pmid, config)

        rate_limit_retries = 0
        validation_retries = 0
        connection_retries = 0

        while True:
            request_id: int | None = None
            try:
                if rate_limiter is not None:
                    request_id = await rate_limiter.acquire(
                        estimated_tokens=config.estimated_tokens_per_call
                    )
                genes = await _stream_and_parse(
                    client,
                    stream_kwargs,
                    usage,
                    pmid=pmid,
                    config=config,
                    rate_limiter=rate_limiter,
                    request_id=request_id,
                )
                return genes, usage

            except anthropic.RateLimitError as e:
                await _release_reservation(rate_limiter, request_id)
                rate_limit_retries = await _retry_rate_limit(
                    e,
                    rate_limit_retries,
                    pmid=pmid,
                    config=config,
                    usage=usage,
                    rate_limiter=rate_limiter,
                )

            except (
                anthropic.APIConnectionError,
                httpx.RemoteProtocolError,
                httpx.ReadError,
                httpx.ConnectError,
            ) as e:
                await _release_reservation(rate_limiter, request_id)
                connection_retries = await _retry_connection(
                    e,
                    connection_retries,
                    pmid=pmid,
                    config=config,
                    usage=usage,
                )

            except (json.JSONDecodeError, ValidationError, ValueError) as e:
                validation_retries = _retry_validation(
                    e,
                    validation_retries,
                    pmid=pmid,
                    config=config,
                    usage=usage,
                )

            except anthropic.APIError as e:
                logger.error(f"Claude API error for PMID {pmid}: {e}")
                raise ExtractionFailedError(
                    f"Claude API error for PMID {pmid}: {e}", usage
                ) from e

            except ExtractionFailedError:
                raise

            except Exception as e:
                logger.error(f"Extraction failed for PMID {pmid}: {e}")
                raise ExtractionFailedError(
                    f"Extraction failed for PMID {pmid}: {e}", usage
                ) from e
