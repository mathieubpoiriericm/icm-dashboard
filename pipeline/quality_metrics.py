"""Pipeline quality metrics tracking.

Provides a lightweight, memory-efficient dataclass for accumulating
metrics during pipeline execution.
"""

from dataclasses import dataclass


@dataclass(slots=True)
class PipelineMetrics:
    """Tracks quality metrics throughout a pipeline run.

    This is a mutable accumulator - metrics are incremented as the
    pipeline processes papers. For thread-safe usage, synchronize
    access externally.

    Attributes:
        papers_processed: Total papers successfully processed.
        fulltext_retrieved: Papers with full text (PMC or Unpaywall).
        abstract_only: Papers where only abstract was available.
        genes_extracted: Raw genes extracted by LLM (pre-validation).
        genes_validated: Genes passing NCBI validation.
        genes_rejected: Genes failing confidence or NCBI checks.
    """

    papers_processed: int = 0
    fulltext_retrieved: int = 0
    abstract_only: int = 0
    genes_extracted: int = 0
    genes_validated: int = 0
    genes_rejected: int = 0

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
        return (
            f"Pipeline Metrics:\n"
            f"  Papers: {self.papers_processed} processed "
            f"({self.fulltext_retrieved} fulltext, {self.abstract_only} abstract-only)\n"
            f"  Fulltext rate: {self.fulltext_rate:.1%}\n"
            f"  Genes: {self.genes_extracted} extracted -> "
            f"{self.genes_validated} validated, {self.genes_rejected} rejected\n"
            f"  Gene acceptance rate: {self.gene_acceptance_rate:.1%}"
        )
