"""Shared types and LLMProvider protocol for extraction backends."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pipeline.config import PipelineConfig
    from pipeline.quality_metrics import TokenUsage
    from pipeline.rate_limiter import AsyncRateLimiter

from pydantic import BaseModel, ConfigDict, Field

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


class ExtractionFailedError(RuntimeError):
    """Raised when an extraction attempt failed rather than found no genes."""

    def __init__(self, message: str, token_usage: TokenUsage | None = None) -> None:
        super().__init__(message)
        self.token_usage = token_usage


# Shared JSON schema: Anthropic feeds this through `transform_schema`, Ollama
# passes it straight to `format=`, prompts.py embeds it as a grounding hint.
EXTRACTION_JSON_SCHEMA: dict[str, Any] = ExtractionResult.model_json_schema()


# ---------------------------------------------------------------------------
# PARSING
# ---------------------------------------------------------------------------


def parse_extraction_response(text: str) -> ExtractionResult:
    """Parse structured output JSON into ExtractionResult.

    With providers that do JSON-schema-constrained decoding (Anthropic's
    output_config, Ollama's `format=schema`), the response is guaranteed
    valid JSON matching the schema — so in practice only Pydantic-level
    validation (e.g. confidence bounds) can fail.

    Raises:
        json.JSONDecodeError: If response is not valid JSON.
        ValidationError: If JSON doesn't satisfy Pydantic constraints.
    """
    data = json.loads(text.strip())
    return ExtractionResult.model_validate(data)


# ---------------------------------------------------------------------------
# PROTOCOL
# ---------------------------------------------------------------------------


@runtime_checkable
class LLMProvider(Protocol):
    """Pluggable backend for gene extraction.

    Implementations:
        - pipeline.llm_providers.anthropic_provider.AnthropicProvider
        - pipeline.llm_providers.ollama_provider.OllamaProvider
    """

    name: str  # "anthropic" or "ollama"

    async def extract(
        self,
        text: str,
        pmid: str,
        config: PipelineConfig,
        rate_limiter: AsyncRateLimiter | None,
    ) -> tuple[list[GeneEntry], TokenUsage]: ...

    async def close(self) -> None: ...

    def supports_thinking(self) -> bool: ...

    def supports_prompt_caching(self) -> bool: ...

    def report_metadata(self, config: PipelineConfig) -> dict[str, Any]:
        """Provider-specific fields for the run-data `pipeline_config` section.

        Keeps report.py from having to branch on provider identity — each
        backend answers with the subset of model/effort/thinking info that
        actually applies to it.
        """
        ...

    def estimate_cost(self, usage: TokenUsage, config: PipelineConfig) -> float | None:
        """Estimate USD cost for a run, or None if pricing isn't tracked."""
        ...
