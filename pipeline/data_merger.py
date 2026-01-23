"""Database merge logic for gene entries.

Handles merging new gene entries into PostgreSQL with batch operations.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, TypedDict, TypeVar

from pipeline.database import (
    get_existing_genes,
    insert_genes_batch,
    update_genes_batch,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


class MergeResult(TypedDict):
    """Result of merge operation."""

    inserted: int
    updated: int


def ensure_list(value: T | list[T] | None) -> list[Any]:
    """Ensure value is a list (handles LLM returning string instead of list).

    Args:
        value: A single value, list of values, or None.

    Returns:
        List containing the value(s), or empty list if None.
    """
    match value:
        case None:
            return []
        case str() as s:
            return [s]
        case list() as lst:
            return lst
        case _:
            return [value]


def dedupe_list(items: Sequence[T]) -> list[T]:
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
    return ";".join(f"{e}*" for e in evidence) + ";" if evidence else ""


def _validate_entry(entry: dict[str, Any]) -> None:
    """Validate required fields in gene entry.

    Args:
        entry: Gene entry dictionary.

    Raises:
        ValueError: If required fields are missing or invalid.
    """
    if not isinstance(entry, dict):
        raise ValueError(f"Entry must be dict, got {type(entry).__name__}")

    if "gene_symbol" not in entry:
        raise ValueError("Entry missing required 'gene_symbol' field")

    if not isinstance(entry["gene_symbol"], str) or not entry["gene_symbol"].strip():
        raise ValueError(f"Invalid gene_symbol: {entry.get('gene_symbol')!r}")


def _build_gene_data(entry: dict[str, Any]) -> dict[str, Any]:
    """Transform pipeline entry to database schema format.

    Args:
        entry: Gene entry from LLM extraction/validation.

    Returns:
        Dictionary matching database schema.
    """
    return {
        "protein": entry.get("protein_name", entry["gene_symbol"]),
        "gene": entry["gene_symbol"],
        "chromosomal_location": "",
        "gwas_trait": ", ".join(dedupe_list(ensure_list(entry.get("gwas_trait")))),
        "mendelian_randomization": "Y" if entry.get("mendelian_randomization") else "",
        "evidence_from_other_omics_studies": format_omics(
            dedupe_list(ensure_list(entry.get("omics_evidence")))
        ),
        "link_to_monogenetic_disease": "",
        "brain_cell_types": "",
        "affected_pathway": "",
        "references": entry.get("pmid", ""),
    }


async def merge_gene_entries(new_entries: Sequence[dict[str, Any]]) -> MergeResult:
    """Merge new gene entries into PostgreSQL using batch operations.

    Database schema (matches R/clean_table1.R expectations):
    - protein, gene, chromosomal_location, gwas_trait,
    - mendelian_randomization, evidence_from_other_omics_studies,
    - link_to_monogenetic_disease, brain_cell_types,
    - affected_pathway, references

    Args:
        new_entries: Sequence of gene entry dictionaries.

    Returns:
        Dictionary with counts of inserted and updated entries.

    Raises:
        ValueError: If any entry is invalid.
    """
    if not new_entries:
        return {"inserted": 0, "updated": 0}

    # Validate all entries upfront (fail fast)
    for i, entry in enumerate(new_entries):
        try:
            _validate_entry(entry)
        except ValueError as e:
            raise ValueError(f"Invalid entry at index {i}: {e}") from e

    existing_genes = await get_existing_genes()

    # Partition entries into inserts vs updates
    to_insert: list[dict[str, Any]] = []
    to_update: list[dict[str, Any]] = []

    for entry in new_entries:
        gene = entry["gene_symbol"].upper()
        gene_data = _build_gene_data(entry)

        if gene in existing_genes:
            to_update.append(gene_data)
        else:
            to_insert.append(gene_data)
            existing_genes.add(gene)  # Prevent duplicates within batch

    # Execute batch operations (2 DB round trips instead of N)
    inserted = await insert_genes_batch(to_insert)
    updated = await update_genes_batch(to_update)

    logger.info(f"Merged genes: {inserted} inserted, {updated} updated")

    return {"inserted": inserted, "updated": updated}
