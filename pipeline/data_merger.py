from typing import List
from pipeline.database import get_existing_genes, insert_gene, update_gene


async def merge_gene_entries(new_entries: List[dict]) -> dict:
    """Merge new gene entries into the PostgreSQL database.

    Database schema (matches R/clean_table1.R expectations):
    - protein, gene, chromosomal_location, gwas_trait,
    - mendelian_randomization, evidence_from_other_omics_studies,
    - link_to_monogenetic_disease, brain_cell_types,
    - affected_pathway, references

    Note: chromosomal_location, link_to_monogenetic_disease, brain_cell_types,
    and affected_pathway are not populated by the pipeline (empty strings).
    These columns are filled separately.

    Returns dict with counts of inserted/updated entries.
    """
    existing_genes = await get_existing_genes()
    inserted, updated = 0, 0

    for entry in new_entries:
        gene = entry["gene_symbol"].upper()
        gene_data = {
            "protein": entry.get("protein_name", entry["gene_symbol"]),
            "gene": entry["gene_symbol"],
            "chromosomal_location": "",
            # Join traits with ", " for R data.table compatibility (R expects single string)
            "gwas_trait": ", ".join(entry.get("gwas_trait") or []),
            # Use "Y"/empty string instead of boolean for R/legacy dashboard format
            "mendelian_randomization": "Y"
            if entry.get("mendelian_randomization")
            else "",
            "evidence_from_other_omics_studies": format_omics(
                entry.get("omics_evidence") or []
            ),
            "link_to_monogenetic_disease": "",
            "brain_cell_types": "",
            "affected_pathway": "",
            # Store PMID as reference; database ON CONFLICT appends new refs with ";"
            "references": entry.get("pmid", ""),
        }

        # Check BEFORE calling database to avoid sequence gaps from ON CONFLICT
        if gene in existing_genes:
            await update_gene(gene_data)
            updated += 1
        else:
            await insert_gene(gene_data)
            inserted += 1

    return {"inserted": inserted, "updated": updated}


def format_omics(evidence: List[str]) -> str:
    """Format omics evidence for database storage.

    Raw format uses '*' suffix and ';' separators.
    R/clean_table1.R strips the '*' and converts ';' to ', '.
    Supported omics types: TWAS, PWAS, EWAS
    """
    # e.g., ["TWAS:brain", "PWAS:blood"] -> "TWAS:brain*;PWAS:blood*;"
    return ";".join(f"{e}*" for e in evidence) + ";" if evidence else ""
