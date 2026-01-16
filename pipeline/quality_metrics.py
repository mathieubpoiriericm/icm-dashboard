from dataclasses import dataclass


@dataclass
class PipelineMetrics:
    """Tracks quality metrics throughout a pipeline run.

    Used for monitoring pipeline health and logging summary statistics.
    Metrics distinguish text retrieval quality from extraction accuracy.
    """

    papers_processed: int = 0  # Total papers successfully processed
    fulltext_retrieved: int = 0  # Papers with full text (PMC or Unpaywall)
    abstract_only: int = 0  # Papers where only abstract was available
    genes_extracted: int = 0  # Raw genes extracted by LLM (pre-validation)
    genes_validated: int = 0  # Genes passing NCBI validation
    genes_rejected: int = 0  # Genes failing confidence or NCBI checks

    @property
    def gene_acceptance_rate(self) -> float:
        """Ratio of validated to extracted genes (0.0 if none extracted)."""
        if self.genes_extracted == 0:
            return 0.0
        return self.genes_validated / self.genes_extracted
