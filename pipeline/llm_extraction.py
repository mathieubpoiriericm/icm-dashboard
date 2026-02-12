"""LLM-based gene extraction using Claude API with streaming.

Extracts genes with putative causal links to cSVD from research papers
using the Anthropic Claude API, with Pydantic-validated structured output.

Uses the streaming API (required for adaptive thinking on Opus 4.6 when
requests may exceed 10 minutes) with JSON schema prompting and Pydantic
validation — replacing the previous Instructor-based approach.

Features:
- Streaming API for long-running adaptive thinking requests
- Pydantic schema enforcement via JSON mode prompting
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
from pydantic import BaseModel, ConfigDict, Field, ValidationError

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
    """Wrapper model for structured extraction."""

    genes: list[GeneEntry] = Field(default_factory=list)


# Pre-compute the JSON schema instruction (identical across all calls).
_EXTRACTION_SCHEMA: str = json.dumps(ExtractionResult.model_json_schema(), indent=2)
_JSON_SCHEMA_INSTRUCTION: str = (
    "As a structured data extraction expert, your task is to understand the "
    "content and provide the parsed objects in JSON that match the following "
    "JSON schema:\n\n"
    f"{_EXTRACTION_SCHEMA}\n\n"
    "Return an instance of the JSON, not the schema itself. "
    "Return ONLY valid JSON with no markdown code fences or surrounding text."
)


# --- Async client singleton ---
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
    """Parse LLM text response into ExtractionResult.

    Handles JSON extraction from text that may include markdown fences
    or surrounding commentary.

    Raises:
        ValueError: If no valid JSON can be extracted.
        ValidationError: If JSON doesn't match ExtractionResult schema.
    """
    text = text.strip()

    # Strip markdown code fences if present
    json_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if json_match:
        text = json_match.group(1).strip()

    # Try direct parse
    try:
        data = json.loads(text)
        return ExtractionResult.model_validate(data)
    except json.JSONDecodeError:
        pass

    # Fallback: find the outermost JSON object
    obj_match = re.search(r"\{[\s\S]*\}", text)
    if obj_match:
        data = json.loads(obj_match.group())
        return ExtractionResult.model_validate(data)

    raise ValueError(f"No valid JSON found in LLM response: {text[:200]}")


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
    )

    # Inject JSON schema instruction between cached extraction instructions
    # and dynamic paper text, so it benefits from prompt caching.
    user_blocks = messages[0]["content"]
    user_blocks.insert(
        1,
        {
            "type": "text",
            "text": _JSON_SCHEMA_INSTRUCTION,
            "cache_control": {"type": "ephemeral"},
        },
    )

    rate_limit_retries = 0
    validation_retries = 0

    while True:
        try:
            # Proactive rate limiting
            if rate_limiter is not None:
                await rate_limiter.acquire(
                    estimated_tokens=config.estimated_tokens_per_call
                )

            # Build streaming kwargs — adaptive thinking dynamically
            # allocates reasoning depth per request.
            stream_kwargs: dict[str, Any] = {
                "model": config.llm_model,
                "max_tokens": config.llm_max_tokens,
                "system": system_blocks,
                "messages": messages,
                "thinking": {"type": "adaptive"},
            }
            if config.llm_effort != "high":
                # "high" is the API default — only send when overridden
                stream_kwargs["output"] = {"effort": config.llm_effort}

            async with client.messages.stream(**stream_kwargs) as stream:
                response = await stream.get_final_message()

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

            # Extract text content from response blocks (skip thinking blocks)
            text_content = ""
            for block in response.content:
                if hasattr(block, "type") and block.type == "text":
                    text_content += block.text

            if not text_content.strip():
                logger.warning(f"Empty text response for PMID {pmid}")
                return [], usage

            # Parse and validate
            result = _parse_extraction_response(text_content)
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

        except (json.JSONDecodeError, ValidationError, ValueError) as e:
            validation_retries += 1
            if validation_retries >= config.max_retries:
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
