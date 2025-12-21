from typing import List, Optional
from pipeline.database import (
    get_existing_genes, get_existing_trials,
    insert_gene, insert_trial
)

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
            "mendelian_randomization": "Y" if entry.get("mendelian_randomization") else "",
            "evidence_from_other_omics_studies": format_omics(entry.get("omics_evidence", [])),
            "link_to_monogenetic_disease": format_omim(entry.get("omim_number")),
            "brain_cell_types": entry.get("brain_cell_types", ""),
            "affected_pathway": entry.get("affected_pathway", ""),
            "references": entry.get("pmid", "")
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

async def merge_trial_entries(new_entries: List[dict]) -> dict:
    """Merge new trial entries into the PostgreSQL database.

    Database schema (matches R/clean_table2.R expectations):
    - drug, mechanism_of_action, genetic_target, genetic_evidence,
    - trial_name, registry_id, clinical_trial_phase,
    - svd_population, svd_population_details, target_sample_size,
    - estimated_completion_date, primary_outcome, sponsor_type

    Returns dict with count of inserted entries.
    """
    existing_trials = await get_existing_trials()
    inserted = 0

    for entry in new_entries:
        registry_id = entry.get("registry_ID", "")

        if registry_id and registry_id in existing_trials:
            continue  # Skip existing trials

        trial_data = {
            "drug": entry["drug"],
            "mechanism_of_action": entry["mechanism_of_action"],
            "genetic_target": entry.get("genetic_target", ""),
            "genetic_evidence": "Yes" if entry.get("genetic_evidence") else "No",
            "trial_name": entry["trial_name"],
            "registry_id": registry_id,
            "clinical_trial_phase": entry.get("clinical_trial_phase", ""),
            "svd_population": entry["svd_population"],
            "svd_population_details": entry.get("svd_population_details", ""),
            "target_sample_size": entry.get("target_sample_size"),
            "estimated_completion_date": entry.get("estimated_completion_date", ""),
            "primary_outcome": entry["primary_outcome"],
            "sponsor_type": entry["sponsor_type"]
        }

        await insert_trial(trial_data)
        inserted += 1

    return {"inserted": inserted}