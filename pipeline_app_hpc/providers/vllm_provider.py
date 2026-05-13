"""vLLM HTTP provider for gene extraction (OpenAI-compatible API)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from json import JSONDecodeError
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import ValidationError

from pipeline.llm_providers.base import (
    EXTRACTION_JSON_SCHEMA,
    ExtractionFailedError,
    GeneEntry,
    parse_extraction_response,
)
from pipeline.prompts import build_extraction_prompt
from pipeline.quality_metrics import TokenUsage
from pipeline.rate_limiter import AsyncRateLimiter, resolve_retry_delay

if TYPE_CHECKING:
    from pipeline.config import PipelineConfig

logger = logging.getLogger(__name__)


async def _record_empty_usage_if_needed(
    rate_limiter: AsyncRateLimiter | None,
    request_id: int | None,
    already_recorded: bool,
) -> None:
    """Release a token reservation when an attempt failed before usage arrived."""
    if rate_limiter is not None and request_id is not None and not already_recorded:
        await rate_limiter.record_actual_usage(request_id, 0)


class VllmProvider:
    """vLLM backend speaking OpenAI-compatible /v1/chat/completions."""

    name = "vllm"

    # Health probe should fail fast — a hung tunnel or unstarted job
    # shouldn't block on the request budget meant for full extractions.
    _HEALTH_TIMEOUT_SECONDS: float = 10.0

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: float = 600.0,
        guided_decoding_backend: str = "outlines",
        base_model_name: str = "",
        adapter_name: str = "",
        max_model_len: int = 0,
        quantization: str = "",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._guided_backend = guided_decoding_backend
        self._base_model_name = base_model_name or model
        self._adapter_name = adapter_name
        self._max_model_len = max_model_len
        self._quantization = quantization
        # Eager construction avoids a concurrent-first-call race that
        # could leak a duplicate client.
        self._client: httpx.AsyncClient | None = httpx.AsyncClient(
            base_url=self._base_url, timeout=timeout
        )
        self._health_checked = False
        self._health_lock = asyncio.Lock()

    def supports_thinking(self) -> bool:
        return False

    def supports_prompt_caching(self) -> bool:
        return False

    def report_metadata(self, config: PipelineConfig) -> dict[str, Any]:
        return {
            "model": self._base_model_name,
            "model_version": "",
            "thinking_mode": "none",
            "effort": None,
            "prompt_version": config.prompt_version,
            "vllm_adapter": self._adapter_name or None,
            "vllm_max_model_len": self._max_model_len,
            "vllm_quantization": self._quantization,
        }

    def estimate_cost(self, usage: TokenUsage, config: PipelineConfig) -> float | None:
        return None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url, timeout=self._timeout
            )
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client. Idempotent."""
        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.aclose()
            self._client = None
        self._health_checked = False

    async def _ensure_healthy(self) -> None:
        """Call /v1/models once per provider lifetime. Fail fast if unreachable."""
        if self._health_checked:
            return
        async with self._health_lock:
            if self._health_checked:
                return
            client = self._get_client()
            try:
                resp = await client.get(
                    "/v1/models", timeout=self._HEALTH_TIMEOUT_SECONDS
                )
                resp.raise_for_status()
            except Exception as e:
                raise RuntimeError(
                    f"vLLM at {self._base_url!r} not reachable. "
                    "Is the SSH tunnel up? Is the sbatch job in 'ready' state?"
                ) from e
            self._health_checked = True

    async def extract(
        self,
        text: str,
        pmid: str,
        config: PipelineConfig,
        rate_limiter: AsyncRateLimiter | None,
    ) -> tuple[list[GeneEntry], TokenUsage]:
        from pipeline.rate_limiter import compute_backoff

        await self._ensure_healthy()
        client = self._get_client()

        prompt = build_extraction_prompt(
            paper_text=text,
            pmid=pmid,
            max_chars=config.max_paper_text_chars,
            prompt_version=config.prompt_version,
        )
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": prompt.combined_system_text},
                {"role": "user", "content": prompt.user_text},
            ],
            "max_tokens": config.llm_max_tokens or 64_000,
            "temperature": 0.0,
            "top_p": 1.0,
            "guided_json": EXTRACTION_JSON_SCHEMA,
            "guided_decoding_backend": self._guided_backend,
        }

        connection_retries = 0
        rate_limit_retries = 0
        validation_attempt = 0
        # Tokens spent on attempts that ultimately failed must still be
        # surfaced to the caller — pipeline.main aggregates these via
        # ExtractionFailedError.token_usage to keep cost reports accurate.
        accumulated_usage = TokenUsage()
        while True:
            request_id: int | None = None
            rate_limiter_recorded = False
            try:
                if rate_limiter is not None:
                    request_id = await rate_limiter.acquire(
                        estimated_tokens=config.estimated_tokens_per_call
                    )
                resp = await client.post("/v1/chat/completions", json=body)
                resp.raise_for_status()
                data = resp.json()

                choice = data["choices"][0]
                finish_reason = choice.get("finish_reason")
                usage_raw = data.get("usage", {})
                response_usage = TokenUsage(
                    input_tokens=usage_raw.get("prompt_tokens", 0),
                    output_tokens=usage_raw.get("completion_tokens", 0),
                )
                if rate_limiter is not None and request_id is not None:
                    await rate_limiter.record_actual_usage(
                        request_id,
                        response_usage.input_tokens + response_usage.output_tokens,
                    )
                    rate_limiter_recorded = True

                # Account for this attempt's tokens up front so any failure
                # path below — truncation, structural KeyError on a malformed
                # choice, JSON/Pydantic validation — still carries the cost in
                # accumulated_usage when the loop exits via ExtractionFailedError.
                accumulated_usage += response_usage

                if finish_reason == "length":
                    accumulated_usage += TokenUsage(truncated_responses=1)
                    raise ExtractionFailedError(
                        f"Response truncated for PMID {pmid} "
                        f"(finish_reason=length). Raise vllm_max_model_len or "
                        f"PIPELINE_LLM_MAX_TOKENS.",
                        accumulated_usage,
                    )

                content = choice["message"]["content"] or ""
                result = parse_extraction_response(content)
                for g in result.genes:
                    g.pmid = pmid
                return result.genes, accumulated_usage

            except (
                JSONDecodeError,
                ValidationError,
                KeyError,
                IndexError,
                TypeError,
            ) as e:
                logger.warning(
                    "PMID %s: vllm validation attempt %d/%d failed: %s",
                    pmid,
                    validation_attempt + 1,
                    config.max_retries + 1,
                    e,
                )
                if validation_attempt >= config.max_retries:
                    raise ExtractionFailedError(
                        f"vllm validation retries exhausted for PMID {pmid}: {e}",
                        accumulated_usage,
                    ) from e
                validation_attempt += 1

            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                if status == 429:
                    rate_limit_retries += 1
                    if rate_limit_retries > config.max_rate_limit_retries:
                        raise ExtractionFailedError(
                            f"vllm rate-limit retries exhausted for PMID "
                            f"{pmid}: {e}",
                            accumulated_usage,
                        ) from e
                    backoff = compute_backoff(
                        config.rate_limit_retry_delay, rate_limit_retries
                    )
                    delay, delay_source = resolve_retry_delay(
                        e.response.headers.get("retry-after"),
                        backoff,
                    )
                    logger.warning(
                        "PMID %s: vllm 429 (%d/%d), retrying in %.1fs (%s)",
                        pmid,
                        rate_limit_retries,
                        config.max_rate_limit_retries,
                        delay,
                        delay_source,
                    )
                    if rate_limiter is not None:
                        await rate_limiter.signal_rate_limit(delay)
                    await asyncio.sleep(delay)
                elif 500 <= status < 600:
                    connection_retries += 1
                    if connection_retries > config.max_connection_retries:
                        raise ExtractionFailedError(
                            f"vllm 5xx retries exhausted for PMID {pmid}: {e}",
                            accumulated_usage,
                        ) from e
                    backoff = compute_backoff(
                        config.connection_retry_delay, connection_retries
                    )
                    logger.warning(
                        "PMID %s: vllm %d (%d/%d), retrying in %.1fs",
                        pmid,
                        status,
                        connection_retries,
                        config.max_connection_retries,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                else:
                    raise ExtractionFailedError(
                        f"vllm HTTP {status} for PMID {pmid}: {e}",
                        accumulated_usage,
                    ) from e

            except httpx.TransportError as e:
                connection_retries += 1
                if connection_retries > config.max_connection_retries:
                    logger.error(
                        "PMID %s: vllm connection retries exhausted (%d/%d): %s",
                        pmid,
                        connection_retries,
                        config.max_connection_retries,
                        e,
                    )
                    raise ExtractionFailedError(
                        f"vllm connection retries exhausted for PMID {pmid}: {e}",
                        accumulated_usage,
                    ) from e
                backoff = compute_backoff(
                    config.connection_retry_delay, connection_retries
                )
                logger.warning(
                    "PMID %s: vllm connection error (%d/%d), retrying in %.1fs: %s",
                    pmid,
                    connection_retries,
                    config.max_connection_retries,
                    backoff,
                    e,
                )
                await asyncio.sleep(backoff)
            finally:
                # Single release point — covers every loop-exit path
                # including CancelledError. `_record_empty_usage_if_needed`
                # is itself idempotent against `rate_limiter_recorded`.
                with contextlib.suppress(Exception):
                    await _record_empty_usage_if_needed(
                        rate_limiter,
                        request_id,
                        rate_limiter_recorded,
                    )
