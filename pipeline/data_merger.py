from typing import List, Optional
from pipeline.database import get_existing_genes, insert_gene


async def merge_gene_entries(new_entries: List[dict]) -> dict:
    """Merge new gene entries into the PostgreSQL database.

    Database schema (matches R/clean_table1.R expectations):
    - protein, gene, chromosomal_location, gwas_trait,
    - mendelian_randomization, evidence_from_other_omics_studies,
    - link_to_monogenetic_disease, brain_cell_types,
    - affected_pathway, references

    Returns dict with counts of inserted/updated entries.
    """
    existing_genes = await get_existing_genes()
    inserted, updated = 0, 0

    for entry in new_entries:
        gene = entry["gene_symbol"].upper()
        gene_data = {
            "protein": entry.get("protein_name", entry["gene_symbol"]),
            "gene": entry["gene_symbol"],
            "chromosomal_location": entry.get("chromosomal_location", ""),
            "gwas_trait": ", ".join(entry.get("gwas_trait", [])),
            "mendelian_randomization": "Y"
            if entry.get("mendelian_randomization")
            else "",
            "evidence_from_other_omics_studies": format_omics(
                entry.get("omics_evidence", [])
            ),
            "link_to_monogenetic_disease": format_omim(entry.get("omim_number")),
            "brain_cell_types": entry.get("brain_cell_types", ""),
            "affected_pathway": entry.get("affected_pathway", ""),
            "references": entry.get("pmid", ""),
        }

        await insert_gene(gene_data)

        if gene in existing_genes:
            updated += 1
        else:
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


def format_omim(omim_number: Optional[str]) -> str:
    """Format OMIM number for link_to_monogenetic_disease column.

    R/clean_table1.R extracts 6-digit OMIM numbers from this field.
    """
    if not omim_number:
        return ""
    return omim_number
