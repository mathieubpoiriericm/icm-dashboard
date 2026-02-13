"""Prompt definitions for LLM-based gene extraction.

Separates prompt engineering from API call logic so prompts can be
iterated on without touching extraction code.
"""

from __future__ import annotations

from typing import Any, Final

SYSTEM_PROMPT: Final[str] = (
    "You are a systematic reviewer specializing in cerebral small vessel disease "
    "(cSVD) genetics. You maintain a curated database of genes with putative "
    "causal links to cSVD, identified through GWAS, multi-omics studies "
    "(TWAS, PWAS, EWAS), Mendelian randomization, colocalization, fine-mapping, "
    "and functional validation. You are rigorous about distinguishing causal "
    "evidence from mere association or incidental mention."
)

EXTRACTION_INSTRUCTIONS: Final[str] = """\
<instructions>
<task>
Extract all genes with putative causal links to cerebral small vessel disease (cSVD) from the research paper provided.
</task>

<inclusion_criteria>
Include a gene when the paper presents ANY evidence suggesting a putative causal relationship with cSVD or cSVD-related phenotypes. Putative causal evidence includes: GWAS associations, fine-mapping, colocalization, Mendelian randomization, functional studies, expression QTLs, animal/cell models, or any mechanistic evidence linking the gene to cSVD pathology.

cSVD-related phenotypes:
- WMH (white matter hyperintensities)
- SVS (small vessel stroke)
- Lacunar stroke / lacunar infarcts / lacunes
- BG-PVS, WM-PVS, HIP-PVS (perivascular spaces)
- Cerebral microbleeds
- PSMD (peak width of skeletonized mean diffusivity)
- FA (fractional anisotropy)
- Other cSVD imaging markers
</inclusion_criteria>

<extraction_strategy>
When analyzing the paper:
1. First identify all passages that mention specific genes in the context of cSVD causality.
2. For each candidate gene, locate the specific evidence described in the paper.
3. Only include genes where you can point to concrete evidence in the text.
4. Exclude genes mentioned only as background, only in citations of other work, or without causal context.
</extraction_strategy>

<field_guidance>
For each gene, record the following:
- gene_symbol: Official HGNC gene symbol.
- gwas_trait: Use ONLY these canonical abbreviations: WMH, SVS, BG-PVS, WM-PVS, HIP-PVS, PSMD, extreme-cSVD, FA, lacunes, stroke. Do not use full phenotype names.
- mendelian_randomization: Set to true only if the paper presents MR evidence for this gene.
- omics_evidence: Type of omics study (e.g., "TWAS", "PWAS", "EWAS", "colocalization").
- confidence: A score from 0.0 to 1.0 (see scoring rubric below).
- causal_evidence_summary: 1-3 sentences explaining WHY the paper considers this gene causally linked.
</field_guidance>

<confidence_scoring>
- 1.0: Direct causal evidence explicitly stated (e.g., functional validation, known monogenic cause)
- 0.7-0.9: Strong causal implication with supporting evidence (e.g., GWAS + colocalization + functional data)
- 0.4-0.6: Mentioned in causal context but evidence is indirect or weak
- Below 0.4: Tangential mention, no clear causal implication
</confidence_scoring>

<examples>
<example type="include">
Paper states: "GWAS identified TRIM47 at 17q25 as significantly associated with WMH volume. TWAS and colocalization in brain tissue support TRIM47 as the causal gene."
Result: gene_symbol="TRIM47", gwas_trait=["WMH"], omics_evidence=["TWAS", "colocalization"], confidence=0.85
</example>

<example type="include_high_confidence">
Paper states: "Mutations in COL4A1 cause monogenic cSVD with WMH and lacunar infarcts. The 13q34 locus is also a GWAS hit for SVS."
Result: gene_symbol="COL4A1", gwas_trait=["SVS", "WMH"], confidence=0.95
</example>

<example type="exclude">
Paper states: "We adjusted for APOE genotype in our GWAS of WMH volume."
Do NOT extract: APOE is only mentioned as a covariate, not as having causal evidence in this paper.
</example>
</examples>
</instructions>"""


def build_extraction_messages(
    paper_text: str,
    pmid: str,
    max_chars: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build system blocks and messages with cache_control for prompt caching.

    Returns:
        (system_blocks, messages) — ready to pass to ``client.messages.create()``.
        The system prompt and extraction instructions are cached in the system
        blocks (prefix match preserved across calls). The user message contains
        only the paper document and a short extraction query.
    """
    system_blocks: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        },
        {
            "type": "text",
            "text": EXTRACTION_INSTRUCTIONS,
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        },
    ]

    user_blocks: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                f'<document source="PubMed" pmid="{pmid}">\n'
                f"{paper_text[:max_chars]}\n"
                f"</document>\n\n"
                "Extract all genes with putative causal links to cSVD "
                "from the document above."
            ),
        },
    ]

    messages: list[dict[str, Any]] = [{"role": "user", "content": user_blocks}]
    return system_blocks, messages
