from typing import List, Optional

import anthropic
from pydantic import BaseModel

client = anthropic.Anthropic()


class GeneEntry(BaseModel):
    gene_symbol: str
    protein_name: Optional[str] = None
    gwas_trait: List[str] = []  # Optional: default empty list
    mendelian_randomization: bool = False  # Optional: default False
    omics_evidence: List[str] = []  # Optional: default empty list
    confidence: float  # Required: 0.0-1.0
    causal_evidence_summary: Optional[str] = None  # Brief explanation of causal evidence


EXTRACTION_PROMPT = """You are an expert in cerebral small vessel disease (cSVD) research and multi-omics studies (genomics, transcriptomics, proteomics, epigenomics).

TASK: Extract genes that are putatively causally linked to cSVD.

PRIMARY CRITERION (REQUIRED):
Include genes where the paper presents ANY evidence suggesting a putative causal relationship with cSVD or cSVD-related phenotypes:
- White matter hyperintensities (WMH)
- Lacunar stroke / lacunar infarcts
- Perivascular spaces (PVS, BG-PVS, WM-PVS)
- Small vessel stroke (SVS)
- Cerebral microbleeds
- PSMD (peak width of skeletonized mean diffusivity)
- Other cSVD imaging markers

Putative causal evidence includes: GWAS associations, fine-mapping, colocalization, Mendelian randomization, functional studies, expression QTLs, animal/cell models, or any mechanistic evidence linking the gene to cSVD pathology.

OPTIONAL SUPPORTING EVIDENCE (record if present, but NOT required):
- gwas_trait: Specific GWAS phenotype associations
- mendelian_randomization: Whether MR evidence supports causality
- omics_evidence: Results from TWAS, PWAS, EWAS, or other omics studies

For each gene, provide a brief causal_evidence_summary explaining WHY it is considered causally linked.

CONFIDENCE SCORING:
- 1.0: Direct causal evidence explicitly stated
- 0.7-0.9: Strong causal implication with supporting context
- 0.4-0.6: Mentioned in causal context but evidence is weak
- <0.4: Tangential mention, no clear causal implication

Return valid JSON with a "genes" array. If no causally-linked genes found, return {"genes": []}."""


def extract_from_paper(text: str, pmid: str) -> List[dict]:
    """Extract genes using Claude API.

    Returns:
        List of gene entries extracted from the paper.
    """
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": f"{EXTRACTION_PROMPT}\n\n---\nPMID: {pmid}\n\n{text[:50000]}",
            }
        ],
    )
    return parse_llm_response(response.content[0].text)


def parse_llm_response(text: str) -> List[dict]:
    """Parse the LLM response JSON into gene entries.

    Handles various response formats:
    - Direct JSON object
    - JSON wrapped in markdown code blocks
    - Partial/malformed JSON with recovery

    Returns:
        List of gene entries (empty list if parsing fails)
    """
    import json
    import re

    if not text or not text.strip():
        return []

    # Try to extract JSON from markdown code blocks
    json_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if json_match:
        text = json_match.group(1)

    # Try direct JSON parsing
    try:
        parsed = json.loads(text.strip())
        if isinstance(parsed, dict):
            return parsed.get("genes", [])
    except json.JSONDecodeError:
        pass

    # Try to find JSON object in the text
    json_obj_match = re.search(r"\{[\s\S]*\}", text)
    if json_obj_match:
        try:
            parsed = json.loads(json_obj_match.group())
            if isinstance(parsed, dict):
                return parsed.get("genes", [])
        except json.JSONDecodeError:
            pass

    # Last resort: try to extract genes array directly
    genes_match = re.search(r'"genes"\s*:\s*(\[[\s\S]*?\])', text)
    if genes_match:
        try:
            return json.loads(genes_match.group(1))
        except json.JSONDecodeError:
            pass

    return []
