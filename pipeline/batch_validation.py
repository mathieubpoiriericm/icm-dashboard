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

from __future__ import annotations

import logging

import pandas as pd
import pandera.pandas as pa

from pipeline.llm_extraction import GeneEntry

logger = logging.getLogger(__name__)

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
        for _, row in e.failure_cases.iterrows():
            warnings.append(
                f"Schema violation in column '{row.get('column', '?')}': "
                f"{row.get('check', '?')} — value: {row.get('failure_case', '?')}"
            )

    # --- Batch-level quality checks ---

    # Check 1: Gene symbol duplication across papers
    # A gene appearing in many papers is expected, but >3 unique papers
    # extracting the same gene in one batch may indicate over-extraction.
    if "pmid" in df.columns and df["pmid"].notna().any():
        gene_paper_counts = (
            df[df["pmid"] != ""].groupby("gene_symbol")["pmid"].nunique()
        )
        for symbol, count in gene_paper_counts.items():
            if count > 3:
                warnings.append(
                    f"Gene '{symbol}' extracted from {count} different papers "
                    f"in this batch (>3 — verify not over-extracted)"
                )

    # Check 2: Confidence distribution
    # A mean confidence > 0.95 across the batch suggests the LLM is not
    # discriminating well between strong and weak evidence.
    mean_confidence = df["confidence"].mean()
    if mean_confidence > 0.95:
        warnings.append(
            f"Mean confidence {mean_confidence:.3f} > 0.95 — "
            f"suspiciously uniform, check LLM calibration"
        )

    # Check 3: Null protein_name rate
    # Protein names should be findable for most genes. A high null rate
    # may indicate the LLM is skipping the protein_name field.
    if "protein_name" in df.columns:
        null_rate = df["protein_name"].isna().mean()
        if null_rate > 0.3:
            warnings.append(
                f"protein_name null rate {null_rate:.1%} > 30% — "
                f"check extraction prompt quality"
            )

    # Check 4: Per-paper gene count sanity
    # A single paper yielding >20 genes is unusual for cSVD literature.
    if "pmid" in df.columns and df["pmid"].notna().any():
        per_paper = df[df["pmid"] != ""].groupby("pmid").size()
        for pmid, count in per_paper.items():
            if count > 20:
                warnings.append(
                    f"PMID {pmid} yielded {count} genes (>20 is unusual — "
                    f"verify extraction quality)"
                )

    # Check 5: Suspiciously long summaries
    # LLMs sometimes hallucinate by copying large chunks of text instead of summarizing.
    if "causal_evidence_summary" in df.columns:
        long_summaries = df[df["causal_evidence_summary"].str.len() > 1000]
        for _, row in long_summaries.iterrows():
            summary_len = len(row["causal_evidence_summary"])
            warnings.append(
                f"Gene '{row['gene_symbol']}' in PMID {row['pmid']} has a "
                f"suspiciously long summary ({summary_len} chars)"
            )

    if warnings:
        logger.warning(f"Batch validation: {len(warnings)} warning(s) raised")
    else:
        logger.info("Batch validation: all checks passed")

    return warnings
