"""Pipeline quality metrics and token usage tracking.

Provides lightweight, memory-efficient dataclasses for accumulating
metrics during pipeline execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class TokenUsage:
    """Accumulated LLM token usage across one or more API calls."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    # Estimated thinking tokens (output_tokens minus text-block tokens).
    # The API does not separate thinking from text in usage counts, so this
    # is set by the caller after inspecting response content blocks.
    thinking_tokens: int = 0

    @property
    def text_output_tokens(self) -> int:
        """Estimated non-thinking output tokens."""
        return max(0, self.output_tokens - self.thinking_tokens)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def cache_hit_rate(self) -> float:
        """Fraction of input tokens served from cache."""
        total_input = self.input_tokens + self.cache_read_input_tokens
        if total_input == 0:
            return 0.0
        return self.cache_read_input_tokens / total_input

    def __iadd__(self, other: TokenUsage) -> TokenUsage:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_creation_input_tokens += other.cache_creation_input_tokens
        self.cache_read_input_tokens += other.cache_read_input_tokens
        self.thinking_tokens += other.thinking_tokens
        return self


def accumulate_usage(usage: TokenUsage, response: Any) -> None:
    """Extract token counts from an Anthropic response and accumulate into *usage*."""
    if not hasattr(response, "usage") or response.usage is None:
        return
    usage.input_tokens += response.usage.input_tokens
    usage.output_tokens += response.usage.output_tokens
    if hasattr(response.usage, "cache_creation_input_tokens"):
        usage.cache_creation_input_tokens += (
            response.usage.cache_creation_input_tokens or 0
        )
    if hasattr(response.usage, "cache_read_input_tokens"):
        usage.cache_read_input_tokens += response.usage.cache_read_input_tokens or 0


@dataclass(slots=True)
class PipelineMetrics:
    """Tracks quality metrics throughout a pipeline run.

    This is a mutable accumulator — metrics are incremented as the
    pipeline processes papers.

    Attributes:
        papers_processed: Total papers successfully processed.
        fulltext_retrieved: Papers with full text (PMC or Unpaywall).
        abstract_only: Papers where only abstract was available.
        genes_extracted: Raw genes extracted by LLM (pre-validation).
        genes_validated: Genes passing NCBI validation.
        genes_rejected: Genes failing confidence or NCBI checks.
        token_usage: Accumulated LLM token usage.
    """

    papers_processed: int = 0
    fulltext_retrieved: int = 0
    abstract_only: int = 0
    genes_extracted: int = 0
    genes_validated: int = 0
    genes_rejected: int = 0
    token_usage: TokenUsage = field(default_factory=TokenUsage)

    @property
    def gene_acceptance_rate(self) -> float:
        """Ratio of validated to extracted genes (0.0 if none extracted)."""
        if (total := self.genes_extracted) == 0:
            return 0.0
        return self.genes_validated / total

    @property
    def fulltext_rate(self) -> float:
        """Ratio of papers with full text to total processed."""
        if (total := self.papers_processed) == 0:
            return 0.0
        return self.fulltext_retrieved / total
