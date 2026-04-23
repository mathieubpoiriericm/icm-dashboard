"""Anthropic Claude backend for gene extraction.

Uses the Anthropic streaming API with adaptive thinking, structured
outputs (JSON Schema constrained decoding), and prompt caching.
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
from pydantic import ValidationError

from pipeline.config import (
    ADAPTIVE_THINKING_MODELS,
    EFFORT_CAPABLE_MODELS,
    THINKING_OUTPUT_RESERVE,
    PipelineConfig,
)
from pipeline.llm_providers.base import (
    ExtractionResult,
    GeneEntry,
    parse_extraction_response,
)
from pipeline.prompts import build_extraction_prompt
from pipeline.quality_metrics import TokenUsage, accumulate_usage
from pipeline.rate_limiter import AsyncRateLimiter, compute_backoff, resolve_retry_delay

# Pricing per 1M tokens (input, output) — bump when a new Claude model ships
# or Anthropic changes published rates. Provider-private because Ollama runs
# don't have a comparable notion of per-token billing.
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


# ---------------------------------------------------------------------------
# PROVIDER
# ---------------------------------------------------------------------------


class AnthropicProvider:
    """Streaming Claude provider (current default)."""

    name = "anthropic"

    def __init__(self) -> None:
        self._client: anthropic.AsyncAnthropic | None = None

    def _get_client(self) -> anthropic.AsyncAnthropic:
        """Lazily build the raw AsyncAnthropic client.

        Raw (not Instructor-wrapped) because we use the streaming API,
        which is required for adaptive-thinking requests that may exceed
        10 minutes of wall-clock time.
        """
        if self._client is None:
            self._client = anthropic.AsyncAnthropic()
        return self._client

    async def close(self) -> None:
        """Close the underlying client. Idempotent."""
        if self._client is not None:
            await self._client.close()
            self._client = None

    def supports_thinking(self) -> bool:
        return True

    def supports_prompt_caching(self) -> bool:
        return True

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

        prompt = build_extraction_prompt(
            paper_text=text,
            pmid=pmid,
            max_chars=config.max_paper_text_chars,
            prompt_version=config.prompt_version,
        )
        system_blocks: list[dict[str, Any]] = [
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
        ]
        messages = [
            {
                "role": "user",
                "content": [{"type": "text", "text": prompt.user_text}],
            }
        ]

        if config.llm_model in ADAPTIVE_THINKING_MODELS:
            # "summarized" keeps thinking blocks populated for the char-ratio
            # estimator below — otherwise models that default to "omitted"
            # return empty thinking text and the split collapses to all-text.
            thinking_config: dict[str, Any] = {
                "type": "adaptive",
                "display": "summarized",
            }
        else:
            budget = max(
                config.llm_max_tokens - THINKING_OUTPUT_RESERVE,
                config.llm_max_tokens // 2,
            )
            thinking_config = {"type": "enabled", "budget_tokens": budget}

        # "high" is the API default — only transmit when overridden.
        output_config = dict(_OUTPUT_CONFIG)
        if config.llm_model in EFFORT_CAPABLE_MODELS and config.llm_effort != "high":
            output_config["effort"] = config.llm_effort

        stream_kwargs: dict[str, Any] = {
            "model": config.llm_model,
            "max_tokens": config.llm_max_tokens,
            "system": system_blocks,
            "messages": messages,
            "thinking": thinking_config,
            "output_config": output_config,
        }

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

                stream_start = time.monotonic()
                async with client.messages.stream(**stream_kwargs) as stream:
                    response = await stream.get_final_message()
                stream_elapsed = time.monotonic() - stream_start

                accumulate_usage(usage, response)

                if (
                    rate_limiter is not None
                    and request_id is not None
                    and response.usage
                ):
                    actual = response.usage.input_tokens + response.usage.output_tokens
                    await rate_limiter.record_actual_usage(request_id, actual)

                # Detect truncation: with adaptive thinking, max_tokens covers
                # both thinking + text output. If thinking consumed most of the
                # budget, the JSON output gets cut off mid-stream. This is a
                # deterministic config problem — retrying with identical
                # max_tokens will always truncate again, so we bail immediately
                # instead of consuming the validation retry budget.
                if response.stop_reason == "max_tokens":
                    used = response.usage.output_tokens if response.usage else "?"
                    logger.error(
                        f"Response truncated for PMID {pmid} "
                        f"(stop_reason=max_tokens, "
                        f"output_tokens={used}/{config.llm_max_tokens}). "
                        f"Raise PIPELINE_LLM_MAX_TOKENS or "
                        f"reduce effort level."
                    )
                    usage.truncated_responses = 1
                    return [], usage

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

                total_chars = thinking_chars + text_chars
                if total_chars > 0 and usage.output_tokens > 0:
                    thinking_ratio = thinking_chars / total_chars
                    usage.thinking_tokens = int(usage.output_tokens * thinking_ratio)

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

                result = parse_extraction_response(text_content)
                # The JSON schema exposes GeneEntry.pmid to the model, so it
                # can emit a (possibly hallucinated) value. Overwrite with the
                # caller's PMID unconditionally, matching OllamaProvider.
                for g in result.genes:
                    g.pmid = pmid
                logger.info(f"Extracted {len(result.genes)} gene(s) from PMID {pmid}")
                return result.genes, usage

            except anthropic.RateLimitError as e:
                # Zero out the unused rate limiter reservation
                if rate_limiter is not None and request_id is not None:
                    await rate_limiter.record_actual_usage(request_id, 0)

                rate_limit_retries += 1
                if rate_limit_retries > config.max_rate_limit_retries:
                    logger.error(
                        f"Rate limit retries exhausted for PMID {pmid} "
                        f"({rate_limit_retries}/{config.max_rate_limit_retries})"
                    )
                    return [], usage

                backoff_delay = compute_backoff(
                    config.rate_limit_retry_delay, rate_limit_retries
                )
                retry_after = (
                    e.response.headers.get("retry-after") if e.response else None
                )
                delay, delay_source = resolve_retry_delay(retry_after, backoff_delay)
                logger.warning(
                    f"Rate limited on PMID {pmid}. "
                    f"Waiting {delay:.1f}s ({delay_source}) "
                    f"(rate limit retry "
                    f"{rate_limit_retries}/{config.max_rate_limit_retries})..."
                )
                if rate_limiter is not None:
                    await rate_limiter.signal_rate_limit(delay)
                await asyncio.sleep(delay)

            except (
                anthropic.APIConnectionError,
                httpx.RemoteProtocolError,
                httpx.ReadError,
                httpx.ConnectError,
            ) as e:
                # Zero out the unused rate limiter reservation
                if rate_limiter is not None and request_id is not None:
                    await rate_limiter.record_actual_usage(request_id, 0)

                connection_retries += 1
                if connection_retries > config.max_connection_retries:
                    logger.error(
                        f"Connection retries exhausted for PMID {pmid} "
                        f"({connection_retries}/{config.max_connection_retries}): {e}"
                    )
                    return [], usage
                backoff_delay = compute_backoff(
                    config.connection_retry_delay, connection_retries
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
