"""Ollama backend for local gene extraction (Gemma 4 by default)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import TYPE_CHECKING

import httpx
import ollama
from pydantic import ValidationError

from pipeline.llm_providers.base import (
    EXTRACTION_JSON_SCHEMA,
    ExtractionResult,
    GeneEntry,
    parse_extraction_response,
)
from pipeline.prompts import build_extraction_prompt
from pipeline.quality_metrics import TokenUsage
from pipeline.rate_limiter import compute_backoff

if TYPE_CHECKING:
    from pipeline.config import PipelineConfig
    from pipeline.rate_limiter import AsyncRateLimiter

logger = logging.getLogger(__name__)


async def close_ollama_client(client: ollama.AsyncClient) -> None:
    """Close the inner httpx transport held by an ollama.AsyncClient.

    The SDK doesn't expose a public close method, so we reach into
    `_client` (its underlying httpx.AsyncClient) and call aclose().
    Errors are swallowed: this is best-effort teardown.
    """
    inner = getattr(client, "_client", None)
    if inner is not None:
        with contextlib.suppress(Exception):
            await inner.aclose()


async def list_available_tags(host: str) -> list[str]:
    """Return tags available on the given Ollama server, or [] on failure.

    Degrades gracefully so callers (like UI dropdowns) can fall back to a
    free-text input when the server isn't reachable.
    """
    client = ollama.AsyncClient(host=host)
    try:
        result = await client.list()
    except Exception:  # noqa: BLE001 — callers must degrade gracefully
        return []
    finally:
        await close_ollama_client(client)
    return [m.model for m in result.models if m.model is not None]


class OllamaProvider:
    """Local Ollama backend using JSON Schema-constrained decoding."""

    name = "ollama"

    def __init__(self, host: str, model: str, num_ctx: int) -> None:
        self._host = host
        self._model = model
        self._num_ctx = num_ctx
        self._client = ollama.AsyncClient(host=host)
        self._health_checked = False
        self._health_lock = asyncio.Lock()

    def supports_thinking(self) -> bool:
        return False

    def supports_prompt_caching(self) -> bool:
        return False

    async def close(self) -> None:
        """Close the underlying HTTP client. Idempotent."""
        await close_ollama_client(self._client)

    async def _ensure_healthy(self) -> None:
        """Call /api/tags once per provider lifetime. Fail fast if unreachable."""
        if self._health_checked:
            return
        async with self._health_lock:
            if self._health_checked:
                return
            try:
                await self._client.list()
            except Exception as e:  # noqa: BLE001 — remap to actionable error
                raise RuntimeError(
                    f"Ollama is not reachable at {self._host!r}. Start it with "
                    f"`ollama serve` or check PIPELINE_OLLAMA_HOST."
                ) from e
            self._health_checked = True

    async def extract(
        self,
        text: str,
        pmid: str,
        config: PipelineConfig,
        rate_limiter: AsyncRateLimiter | None,
    ) -> tuple[list[GeneEntry], TokenUsage]:
        await self._ensure_healthy()

        prompt = build_extraction_prompt(
            paper_text=text,
            pmid=pmid,
            max_chars=config.max_paper_text_chars,
            prompt_version=config.prompt_version,
        )
        system_text = f"{prompt.system_prompt}\n\n{prompt.extraction_instructions}"

        result: ExtractionResult | None = None
        response: ollama.ChatResponse | None = None
        connection_retries = 0
        validation_attempt = 0
        while True:
            try:
                response = await self._client.chat(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": system_text},
                        {"role": "user", "content": prompt.user_text},
                    ],
                    format=EXTRACTION_JSON_SCHEMA,
                    options={
                        "num_ctx": self._num_ctx,
                        "num_predict": (
                            config.llm_max_tokens if config.llm_max_tokens else -1
                        ),
                        "temperature": 0.0,
                        "top_p": 1.0,
                    },
                    keep_alive="30m",
                    stream=False,
                )
                raw = response.message.content or ""
                result = parse_extraction_response(raw)
                break
            except (json.JSONDecodeError, ValidationError) as e:
                logger.warning(
                    "PMID %s: ollama validation attempt %d/%d failed: %s",
                    pmid,
                    validation_attempt + 1,
                    config.max_retries + 1,
                    e,
                )
                if validation_attempt >= config.max_retries:
                    raise
                validation_attempt += 1
                # No backoff — local server; nothing to wait on.
            except (
                httpx.ConnectError,
                httpx.ReadError,
                httpx.RemoteProtocolError,
            ) as e:
                connection_retries += 1
                if connection_retries > config.max_connection_retries:
                    logger.error(
                        "PMID %s: ollama connection retries exhausted (%d/%d): %s",
                        pmid,
                        connection_retries,
                        config.max_connection_retries,
                        e,
                    )
                    raise
                backoff = compute_backoff(
                    config.connection_retry_delay, connection_retries
                )
                logger.warning(
                    "PMID %s: ollama connection error (%d/%d), retrying in %.1fs: %s",
                    pmid,
                    connection_retries,
                    config.max_connection_retries,
                    backoff,
                    e,
                )
                await asyncio.sleep(backoff)

        # result and response are guaranteed non-None here because the only loop
        # exits are `break` (both set) or `raise` (propagates).
        assert result is not None and response is not None

        for g in result.genes:
            g.pmid = pmid

        usage = TokenUsage(
            input_tokens=response.prompt_eval_count or 0,
            output_tokens=response.eval_count or 0,
        )
        return result.genes, usage
