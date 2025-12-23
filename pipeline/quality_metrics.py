from dataclasses import dataclass


@dataclass
class PipelineMetrics:
    papers_processed: int = 0
    fulltext_retrieved: int = 0
    abstract_only: int = 0
    genes_extracted: int = 0
    genes_validated: int = 0
    genes_rejected: int = 0
    trials_extracted: int = 0
    trials_validated: int = 0
    trials_rejected: int = 0

    @property
    def gene_acceptance_rate(self) -> float:
        if self.genes_extracted == 0:
            return 0.0
        return self.genes_validated / self.genes_extracted
