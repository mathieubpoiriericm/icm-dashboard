"""Database merge logic for gene entries.

Handles merging new gene entries into PostgreSQL with batch operations.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, TypedDict

from pipeline.database import (
    get_existing_genes,
    merge_genes_transactional,
)
from pipeline.llm_extraction import GeneEntry

logger = logging.getLogger(__name__)


class MergeResult(TypedDict):
    """Result of merge operation."""

    inserted: int
    updated: int


def dedupe_list[T](items: Sequence[T]) -> list[T]:
    """Remove duplicates from sequence while preserving order.

    Args:
        items: Input sequence (list, tuple, etc.).

    Returns:
        New list with duplicates removed, maintaining first occurrence order.
    """
    seen: set[Any] = set()
    result: list[T] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def format_omics(evidence: Sequence[str]) -> str:
    """Format omics evidence for database storage.

    Raw format uses '*' suffix and ';' separators.
    R/clean_table1.R strips the '*' and converts ';' to ', '.
    Supported omics types: TWAS, PWAS, EWAS

    Args:
        evidence: List of omics evidence strings.

    Returns:
        Formatted string for database storage.
    """
    return ";".join(f"{e}*" for e in evidence) if evidence else ""


def _build_combined_gene_data(entries: Sequence[GeneEntry]) -> dict[str, Any]:
    """Combine one or more entries for the same gene into a single DB row.

    When a batch contains multiple entries for the same gene (e.g. mentions
    in several papers), the per-field values are merged/deduped rather than
    overwriting one another.
    """
    first = entries[0]
    all_gwas: list[str] = []
    all_omics: list[str] = []
    all_pmids: list[str] = []
    protein_name: str | None = None
    has_mr = False
    for entry in entries:
        all_gwas.extend(entry.gwas_trait)
        all_omics.extend(entry.omics_evidence)
        if entry.pmid:
            all_pmids.append(entry.pmid)
        if protein_name is None and entry.protein_name:
            protein_name = entry.protein_name
        if entry.mendelian_randomization:
            has_mr = True

    return {
        "protein": protein_name or first.gene_symbol,
        "gene": first.gene_symbol,
        "chromosomal_location": "",
        "gwas_trait": ", ".join(dedupe_list(all_gwas)),
        "mendelian_randomization": "Y" if has_mr else "",
        "evidence_from_other_omics_studies": format_omics(dedupe_list(all_omics)),
        "link_to_monogenetic_disease": "",
        "brain_cell_types": "",
        "affected_pathway": "",
        "references": "; ".join(dedupe_list(all_pmids)),
    }


async def merge_gene_entries(new_entries: Sequence[GeneEntry]) -> MergeResult:
    """Merge new gene entries into PostgreSQL using transactional batch operations.

    Database schema (matches R/clean_table1.R expectations):
    - protein, gene, chromosomal_location, gwas_trait,
    - mendelian_randomization, evidence_from_other_omics_studies,
    - link_to_monogenetic_disease, brain_cell_types,
    - affected_pathway, references

    Args:
        new_entries: Sequence of validated GeneEntry instances.

    Returns:
        Dictionary with counts of inserted and updated entries.
    """
    if not new_entries:
        return {"inserted": 0, "updated": 0}

    existing_genes = await get_existing_genes()

    # Collapse per-gene duplicates in the batch before splitting into
    # insert/update — otherwise the UPDATE would overwrite gwas_trait /
    # omics / protein from the first occurrence.
    grouped: dict[str, list[GeneEntry]] = {}
    for entry in new_entries:
        grouped.setdefault(entry.gene_symbol.upper(), []).append(entry)

    to_insert: list[dict[str, Any]] = []
    to_update: list[dict[str, Any]] = []

    for gene_upper, entries_for_gene in grouped.items():
        gene_data = _build_combined_gene_data(entries_for_gene)
        if gene_upper in existing_genes:
            to_update.append(gene_data)
        else:
            to_insert.append(gene_data)

    inserted, updated = await merge_genes_transactional(to_insert, to_update)

    logger.info(f"Merged genes: {inserted} inserted, {updated} updated")

    return {"inserted": inserted, "updated": updated}
