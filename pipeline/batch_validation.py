"""Batch-level quality validation using Pandera.

Runs quality checks across the entire batch of extracted genes before
database merge. Initially warning-only — tune thresholds over 2-3 runs
before upgrading to blocking.

Checks:
1. Gene symbol duplication across papers (>3 papers = suspicious)
2. Confidence distribution (mean > 0.95 = suspiciously uniform)
3. Null rate thresholds (>30% null protein_name = check prompt quality)
4. Per-paper gene count sanity (>20 genes from one paper = unusual)
"""

import logging
from collections import Counter, defaultdict

import pandas as pd
import pandera.pandas as pa

from pipeline.llm_providers.base import GeneEntry

logger = logging.getLogger(__name__)

_MAX_PAPERS_PER_GENE = 3
_MAX_MEAN_CONFIDENCE = 0.95
_MAX_NULL_PROTEIN_RATE = 0.3
_MAX_GENES_PER_PAPER = 20
_MAX_SUMMARY_LENGTH = 1000

# Pandera schema for individual gene entry validation within the batch.
# This catches any data quality issues that slipped past Pydantic
# (e.g., empty strings that pass str validation).
BATCH_SCHEMA = pa.DataFrameSchema(
    columns={
        "gene_symbol": pa.Column(
            str,
            pa.Check.str_length(min_value=1),
            nullable=False,
        ),
        "confidence": pa.Column(
            float,
            pa.Check.in_range(0.0, 1.0),
        ),
        "protein_name": pa.Column(str, nullable=True, required=False),
        "pmid": pa.Column(str, required=False),
    },
    strict=False,  # Allow extra columns without failing
)


def batch_validate(genes: list[GeneEntry]) -> list[str]:
    """Run batch-level quality checks on extracted genes.

    Returns a list of warning strings. Currently warning-only (does not
    raise exceptions). Intended to be upgraded to blocking after threshold
    tuning over 2-3 production runs.

    Args:
        genes: List of validated GeneEntry instances (with pmid set).

    Returns:
        List of warning messages (empty if all checks pass).
    """
    if not genes:
        return []

    warnings: list[str] = []

    # Build DataFrame for Pandera + aggregate checks
    records = [
        {
            "gene_symbol": g.gene_symbol,
            "confidence": g.confidence,
            "protein_name": g.protein_name,
            "pmid": g.pmid,
            "causal_evidence_summary": g.causal_evidence_summary,
        }
        for g in genes
    ]
    df = pd.DataFrame(records)

    # --- Pandera schema validation ---
    try:
        BATCH_SCHEMA.validate(df, lazy=True)
    except pa.errors.SchemaErrors as e:
        warnings.extend(
            f"Schema violation in column '{row.get('column', '?')}': "
            f"{row.get('check', '?')} — value: {row.get('failure_case', '?')}"
            for row in e.failure_cases.to_dict("records")
        )

    # --- Batch-level quality checks ---

    # Check 1: Gene symbol duplication across papers
    # A gene appearing in many papers is expected, but >3 unique papers
    # extracting the same gene in one batch may indicate over-extraction.
    papers_by_gene: defaultdict[str, set[str]] = defaultdict(set)
    for gene in genes:
        if gene.pmid:
            papers_by_gene[gene.gene_symbol].add(gene.pmid)
    warnings.extend(
        f"Gene '{symbol}' extracted from {count} different papers "
        f"in this batch (>{_MAX_PAPERS_PER_GENE} — verify not over-extracted)"
        for symbol, paper_ids in papers_by_gene.items()
        if (count := len(paper_ids)) > _MAX_PAPERS_PER_GENE
    )

    # Check 2: Confidence distribution
    # A mean confidence > 0.95 across the batch suggests the LLM is not
    # discriminating well between strong and weak evidence.
    mean_confidence = sum(gene.confidence for gene in genes) / len(genes)
    if mean_confidence > _MAX_MEAN_CONFIDENCE:
        warnings.append(
            f"Mean confidence {mean_confidence:.3f} > {_MAX_MEAN_CONFIDENCE} — "
            f"suspiciously uniform, check LLM calibration"
        )

    # Check 3: Null protein_name rate
    # Protein names should be findable for most genes. A high null rate
    # may indicate the LLM is skipping the protein_name field.
    null_rate = sum(gene.protein_name is None for gene in genes) / len(genes)
    if null_rate > _MAX_NULL_PROTEIN_RATE:
        warnings.append(
            f"protein_name null rate {null_rate:.1%} > 30% — "
            f"check extraction prompt quality"
        )

    # Check 4: Per-paper gene count sanity
    # A single paper yielding >20 genes is unusual for cSVD literature.
    genes_per_paper = Counter(gene.pmid for gene in genes if gene.pmid)
    warnings.extend(
        f"PMID {pmid} yielded {count} genes "
        f"(>{_MAX_GENES_PER_PAPER} is unusual — verify extraction quality)"
        for pmid, count in genes_per_paper.items()
        if count > _MAX_GENES_PER_PAPER
    )

    # Check 5: Suspiciously long summaries
    # LLMs sometimes hallucinate by copying large chunks of text instead of summarizing.
    warnings.extend(
        f"Gene '{gene.gene_symbol}' in PMID {gene.pmid} has a "
        f"suspiciously long summary ({len(gene.causal_evidence_summary)} chars)"
        for gene in genes
        if gene.causal_evidence_summary is not None
        and len(gene.causal_evidence_summary) > _MAX_SUMMARY_LENGTH
    )

    if warnings:
        logger.warning(f"Batch validation: {len(warnings)} warning(s) raised")
    else:
        logger.info("Batch validation: all checks passed")

    return warnings
