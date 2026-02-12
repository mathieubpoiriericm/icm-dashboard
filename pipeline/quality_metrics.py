"""Pipeline quality metrics and token usage tracking.

Provides lightweight, memory-efficient dataclasses for accumulating
metrics during pipeline execution, plus a structured JSON report builder.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class TokenUsage:
    """Accumulated LLM token usage across one or more API calls."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

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

    @property
    def total_genes_processed(self) -> int:
        """Total genes that went through validation (validated + rejected)."""
        return self.genes_validated + self.genes_rejected

    def summary(self) -> str:
        """Return formatted summary string for logging."""
        lines = [
            "Pipeline Metrics:",
            f"  Papers: {self.papers_processed} processed "
            f"({self.fulltext_retrieved} fulltext, {self.abstract_only} abstract-only)",
            f"  Fulltext rate: {self.fulltext_rate:.1%}",
            f"  Genes: {self.genes_extracted} extracted -> "
            f"{self.genes_validated} validated, {self.genes_rejected} rejected",
            f"  Gene acceptance rate: {self.gene_acceptance_rate:.1%}",
        ]

        tu = self.token_usage
        if tu.total_tokens > 0:
            lines.append(
                f"  Tokens: {tu.input_tokens:,} input + {tu.output_tokens:,} output "
                f"= {tu.total_tokens:,} total"
            )
            if tu.cache_read_input_tokens > 0 or tu.cache_creation_input_tokens > 0:
                lines.append(
                    f"  Cache: {tu.cache_read_input_tokens:,} read, "
                    f"{tu.cache_creation_input_tokens:,} created "
                    f"(hit rate: {tu.cache_hit_rate:.1%})"
                )

        return "\n".join(lines)

    def build_report(self) -> dict[str, Any]:
        """Build a structured report dict suitable for JSON serialisation."""
        return {
            "timestamp": datetime.now(UTC).isoformat(),
            "papers": {
                "processed": self.papers_processed,
                "fulltext_retrieved": self.fulltext_retrieved,
                "abstract_only": self.abstract_only,
                "fulltext_rate": round(self.fulltext_rate, 4),
            },
            "genes": {
                "extracted": self.genes_extracted,
                "validated": self.genes_validated,
                "rejected": self.genes_rejected,
                "acceptance_rate": round(self.gene_acceptance_rate, 4),
            },
            "token_usage": asdict(self.token_usage),
        }

    def write_json_report(self, log_dir: Path) -> Path:
        """Write the structured report as JSON to *log_dir*.

        Returns the path to the written file.
        """
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        path = log_dir / f"pipeline_report_{stamp}.json"
        path.write_text(json.dumps(self.build_report(), indent=2) + "\n")
        return path
