from typing import List, Optional

import anthropic
from pydantic import BaseModel

client = anthropic.Anthropic()

class GeneEntry(BaseModel):
    gene_symbol: str
    protein_name: Optional[str]
    gwas_trait: List[str]
    mendelian_randomization: bool
    omics_evidence: List[str]
    omim_number: Optional[str]
    brain_cell_types: Optional[str]
    affected_pathway: Optional[str]
    confidence: float  # 0.0-1.0

class TrialEntry(BaseModel):
    drug: str
    mechanism_of_action: str
    genetic_target: Optional[str]  # Singular, matches CSV
    genetic_evidence: bool
    trial_name: str
    registry_ID: Optional[str]  # Note: uppercase ID matches CSV
    clinical_trial_phase: Optional[str]
    svd_population: str
    svd_population_details: Optional[str]
    target_sample_size: Optional[int]
    estimated_completion_date: Optional[str]
    primary_outcome: str
    sponsor_type: str
    confidence: float

EXTRACTION_PROMPT = """You are extracting structured data from an SVD research paper.

TASK: Extract genes and clinical trials relevant to cerebral small vessel disease.

For GENES, extract entries where the gene is:
- Identified in GWAS for SVD phenotypes (WMH, SVS, lacunar stroke, PVS, etc.)
- Supported by Mendelian randomization evidence
- Identified in TWAS, PWAS, EWAS, or other omics studies
- Linked to monogenic SVD conditions

For CLINICAL TRIALS, extract drugs being tested for:
- SVD-related conditions (CADASIL, CAA, lacunar stroke, vascular dementia)
- Include registry IDs (NCT, ISRCTN, ACTRN, ChiCTR)

CONFIDENCE SCORING:
- 1.0: Explicit statement with clear evidence
- 0.7-0.9: Strong implication with supporting context
- 0.4-0.6: Mentioned but evidence unclear
- <0.4: Tangential mention, likely not relevant

Return valid JSON matching the schema. If no relevant data, return empty arrays."""

def extract_from_paper(text: str, pmid: str) -> dict:
    """Extract genes and trials using Claude API."""
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": f"{EXTRACTION_PROMPT}\n\n---\nPMID: {pmid}\n\n{text[:50000]}"
        }]
    )
    return parse_llm_response(response.content[0].text)


def parse_llm_response(text: str) -> dict:
    """Parse the LLM response JSON into structured data."""
    raise NotImplementedError(f"parse_llm_response not implemented for text: {text[:50]}")