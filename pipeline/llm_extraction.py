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


EXTRACTION_PROMPT = """You are extracting structured data from an SVD research paper.

TASK: Extract genes relevant to cerebral small vessel disease (cSVD).

Extract gene entries where the gene is:
- Identified in GWAS for SVD phenotypes (WMH, SVS, lacunar stroke, PVS, etc.)
- Supported by Mendelian randomization evidence
- Identified in TWAS, PWAS, EWAS, or other omics studies
- Linked to monogenic SVD conditions (e.g., CADASIL, CARASIL, Fabry disease)

CONFIDENCE SCORING:
- 1.0: Explicit statement with clear evidence
- 0.7-0.9: Strong implication with supporting context
- 0.4-0.6: Mentioned but evidence unclear
- <0.4: Tangential mention, likely not relevant

Return valid JSON with a "genes" array. If no relevant genes found, return {"genes": []}."""


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
