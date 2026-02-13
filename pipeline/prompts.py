"""Prompt definitions for LLM-based gene extraction.

Separates prompt engineering from API call logic so prompts can be
iterated on without touching extraction code.
"""

from __future__ import annotations

from typing import Any, Final

SYSTEM_PROMPT: Final[str] = (
    "You are an expert in cerebral small vessel disease (cSVD) research "
    "and multi-omics studies (genomics, transcriptomics, proteomics, epigenomics)."
)

EXTRACTION_INSTRUCTIONS: Final[str] = """\
TASK: Extract genes that are putatively causally linked to cSVD.

PRIMARY CRITERION (REQUIRED):
Include genes where the paper presents ANY evidence suggesting a putative causal relationship with cSVD or cSVD-related phenotypes:
- White matter hyperintensities (WMH)
- Lacune
- White matter lesion
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
- Confidence must be a number between 0.0 and 1.0 inclusive.
- 1.0: Direct causal evidence explicitly stated
- 0.7-0.9: Strong causal implication with supporting context
- 0.4-0.6: Mentioned in causal context but evidence is weak
- <0.4: Tangential mention, no clear causal implication

If no causally-linked genes are found, return an empty list for genes."""


def build_extraction_messages(
    paper_text: str,
    pmid: str,
    max_chars: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build system blocks and messages with cache_control for prompt caching.

    Returns:
        (system_blocks, messages) — ready to pass to ``client.messages.create()``.
        The system prompt and static instruction prefix are marked with
        ``cache_control: {"type": "ephemeral"}`` so Anthropic caches them
        across calls within the same session.
    """
    system_blocks: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        },
    ]

    # Static prefix (cacheable) + dynamic paper text (not cached)
    user_blocks: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": EXTRACTION_INSTRUCTIONS,
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        },
        {
            "type": "text",
            "text": f"---\nPMID: {pmid}\n\n{paper_text[:max_chars]}",
        },
    ]

    messages: list[dict[str, Any]] = [{"role": "user", "content": user_blocks}]
    return system_blocks, messages
