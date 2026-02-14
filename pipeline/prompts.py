"""Prompt definitions for LLM-based gene extraction.

Separates prompt engineering from API call logic so prompts can be
iterated on without touching extraction code.
"""

from __future__ import annotations

from typing import Any, Final

SYSTEM_PROMPT: Final[str] = (
    "You are a systematic reviewer specializing in cerebral small vessel disease "
    "(cSVD) genetics. You maintain a curated database of genes with putative "
    "causal links to cSVD, identified through GWAS, MAGMA gene-based tests, "
    "multi-trait GWAS (MTAG), multi-omics studies (TWAS, PWAS, EWAS, proteomics), "
    "Mendelian randomization, colocalization, fine-mapping, proteogenomics/pQTL-MR, "
    "WES/WGS rare variant burden tests, cell-type enrichment analyses, "
    "multi-ancestry GWAS, and functional validation. You are rigorous about "
    "distinguishing causal evidence from mere association or incidental mention. "
    "You carefully distinguish cSVD-specific evidence (small vessel stroke, WMH, "
    "lacunes, PVS, microbleeds) from general stroke or neurodegeneration findings."
)

EXTRACTION_INSTRUCTIONS: Final[str] = """\
<instructions>
<task>
Extract all genes with putative causal links to cerebral small vessel disease (cSVD) from the research paper provided.
</task>

<inclusion_criteria>
Include a gene when the paper presents ANY evidence suggesting a putative causal relationship with cSVD or cSVD-related phenotypes. Putative causal evidence includes: GWAS associations, MAGMA gene-based tests, multi-trait GWAS (MTAG), fine-mapping, colocalization, Mendelian randomization, pQTL-MR/proteogenomics, WES/WGS rare variant burden tests, cell-type enrichment analyses, multi-ancestry GWAS, polygenic risk scores (PRS), functional studies, expression QTLs, animal/cell models, or any mechanistic evidence linking the gene to cSVD pathology.

Primary cSVD phenotypes:
- WMH (white matter hyperintensities)
- DWMH (deep WMH), PVWMH (periventricular WMH)
- SVS (small vessel stroke)
- Lacunar stroke / lacunar infarcts / lacunes
- BG-PVS, WM-PVS, HIP-PVS (perivascular spaces)
- Cerebral microbleeds
- ICH-lobar (lobar intracerebral hemorrhage), ICH-non-lobar (non-lobar intracerebral hemorrhage)
- PSMD (peak width of skeletonized mean diffusivity)
- MD (mean diffusivity)
- FA (fractional anisotropy)
- DTI-ALPS (glymphatic function marker)
- ICVF (neurite density), ISOVF (free-water volume fraction), OD (orientation dispersion) — NODDI metrics
- WMH-cortical-atrophy (WMH-associated cortical atrophy)

Secondary cSVD-related phenotypes:
- Retinal vessel phenotypes (retinal-vessels: tortuosity, caliber)
- WM-BAG (white matter brain age gap)
</inclusion_criteria>

<extraction_strategy>
When analyzing the paper:
1. Identify all passages that mention specific genes in the context of cSVD causality.
2. Verify that each gene was tested in a cSVD-specific analysis (SVS, WMH, lacunes, PVS, microbleeds, or another cSVD phenotype) — not just general stroke, cardioembolic stroke, or large-artery stroke.
3. Distinguish genes with individual statistical evidence (GWAS hit, gene-based significance, MR result) from genes mentioned only in pathway enrichment analyses (e.g., listed in an ECM gene set). Only extract pathway-only genes if the paper explicitly discusses their individual role.
4. For well-known monogenic cSVD genes (NOTCH3, COL4A1, COL4A2, HTRA1, TREX1, GLA), only extract if the paper presents NEW data — new statistical association, new functional evidence, or new population-level variant analysis. Do NOT extract these genes from background sentences like "CADASIL is caused by NOTCH3 mutations."
5. At multi-gene loci (e.g., 17q25: TRIM47/TRIM65; 2q33: NBEAL1/WDR12; 13q34: COL4A1/COL4A2), list ALL candidate genes the paper discusses with per-gene evidence types.
6. Parse for negative results ("did not support," "no association," "failed to replicate"). If a gene was tested and found not associated, assign confidence 0 and note the negative result.
7. Distinguish MR-exposure genes (e.g., "genetically proxied ACE inhibition reduces WMH") from genes with direct cSVD genetic association. Flag MR-only evidence in the causal_evidence_summary.
8. Map animal model gene nomenclature to human HGNC symbols (e.g., mouse Trim47 → TRIM47, zebrafish col4a1 → COL4A1).
</extraction_strategy>

<field_guidance>
For each gene, record the following:
- gene_symbol: Official HGNC gene symbol (human). Convert animal model nomenclature to human orthologs.
- gwas_trait: Use ONLY these canonical abbreviations: WMH, DWMH, PVWMH, SVS, BG-PVS, WM-PVS, HIP-PVS, PSMD, MD, FA, extreme-cSVD, lacunes, stroke, cerebral-microbleeds, ICH-lobar, ICH-non-lobar, DTI-ALPS, ICVF, ISOVF, OD, WMH-cortical-atrophy, WM-BAG, retinal-vessels. Do not use full phenotype names.
- mendelian_randomization: Set to true only if the paper presents MR evidence for this gene. In causal_evidence_summary, distinguish whether the gene is an MR exposure (drug target) vs. a direct cSVD-associated gene.
- omics_evidence: Type of omics/analytical study. Use labels such as: "TWAS", "PWAS", "EWAS", "colocalization", "MAGMA", "MTAG", "pQTL-MR", "WES/WGS", "cell-type enrichment", "scRNA-seq annotation", "PRS", "fine-mapping".
- confidence: A score from 0.0 to 1.0 (see scoring rubric below).
- causal_evidence_summary: 1-3 sentences explaining WHY the paper considers this gene causally linked. Note stroke-subtype specificity (cSVD-specific vs. general stroke). For MR evidence, clarify whether the gene is a direct association or an exposure/drug target.
</field_guidance>

<confidence_scoring>
Six-tier rubric:

- 1.0: Validated causal — established monogenic cause with understood mechanism (e.g., NOTCH3, COL4A1/COL4A2, HTRA1) OR GWAS significance + fine-mapping to credible set of ≤5 variants + functional validation in a cSVD-relevant cell type (brain endothelial, pericyte, VSMC).
- 0.8-0.9: Strong multi-modal convergence — GWAS significance for a cSVD-specific phenotype + eQTL/pQTL colocalization in relevant tissue + at least one supporting line (coding variant, animal model, rare variant burden, or drug target confirmation).
- 0.6-0.7: Moderate with partial support — GWAS significance + TWAS or positional candidacy + plausible biological pathway, but without full colocalization or functional validation. Also applies to MTAG-only findings without independent replication.
- 0.4-0.5: Indirect or suggestive — pathway analysis gene only (enriched gene set member, no individual significance); pQTL-MR evidence without direct genetic association; general stroke GWAS gene without cSVD-specific evidence; or animal model findings without human genetic support. pQTL-MR with cis instruments scores at the high end (0.5).
- 0.2-0.3: Weak or contextual — genetic correlation only; unreplicated candidate gene study; pre-GWAS era association; protein interaction network member without direct evidence.
- 0.0: Negative or tangential — gene tested and found not associated; background citation only; covariate mention; general context sentence with no causal evidence.

Cross-cutting modifiers:
- Stroke-specificity penalty: Apply −0.1 to −0.2 for genes from general stroke GWAS without SVS/WMH/cSVD-specific replication.
- Monogenic-to-sporadic: When a known monogenic cSVD gene (NOTCH3, COL4A1/A2, HTRA1) has NEW common-variant evidence, score the new evidence independently of monogenic status.
</confidence_scoring>

<examples>
<example type="include_validated">
Paper states: "GWAS identified TRIM47 at 17q25 as significantly associated with WMH volume. SMR/HEIDI colocalization confirmed TRIM47 as the causal gene. siRNA knockdown in brain endothelial cells increased permeability. Trim47-deficient mice show BBB dysfunction and cognitive impairment via the KEAP1-NRF2 pathway, rescued by the NRF2 activator tBHQ."
Result: gene_symbol="TRIM47", gwas_trait=["WMH"], omics_evidence=["TWAS", "colocalization"], confidence=1.0
Reasoning: Full GWAS → eQTL → functional → animal model → therapeutic target chain = validated causal.
</example>

<example type="include_high_confidence">
Paper states: "Rare COL4A1 mutations cause Gould syndrome with cSVD features. At 13q34, common variants reach genome-wide significance for WMH, non-lobar ICH, and SVS. The rs9515201 variant is a cis-eQTL for both COL4A1 and COL4A2 in brain putamen."
Result: gene_symbol="COL4A1", gwas_trait=["WMH", "ICH-non-lobar", "SVS"], confidence=0.95; also gene_symbol="COL4A2", gwas_trait=["WMH", "ICH-non-lobar", "SVS"], confidence=0.95
Reasoning: Monogenic + common-variant GWAS for multiple cSVD traits. Both genes at the locus extracted with per-gene evidence.
</example>

<example type="include_strong_functional">
Paper states: "Endothelial-specific Foxf2 deletion in mice causes BBB leakage and impaired functional hyperemia. Multi-omic analysis identified FOXF2 as a transcriptional activator of Tie2/TEK signaling. A Tie2 agonist rescued all phenotypes. FOXF2 was previously identified in GWAS for SVS and WMH."
Result: gene_symbol="FOXF2", gwas_trait=["SVS", "WMH"], confidence=0.9
Reasoning: GWAS + comprehensive functional validation with therapeutic rescue in cSVD-relevant cell type.
</example>

<example type="exclude_general_stroke">
Paper states: "PITX2 and ZFHX3 reached genome-wide significance for cardioembolic stroke in MEGASTROKE. We included these in our comparison of stroke subtypes."
Do NOT extract: PITX2 and ZFHX3 are cardioembolic-specific. No evidence for SVS, WMH, or any cSVD phenotype.
</example>

<example type="exclude_pathway_only">
Paper states: "Gene set enrichment analysis identified the extracellular matrix pathway (GO:0031012) as significantly enriched among our GWAS hits. This set includes 47 genes including FBN1, LAMA2, and COL6A3."
Do NOT extract FBN1, LAMA2, COL6A3: These genes are mentioned only as members of an enriched pathway. No individual-level statistical significance or causal evidence is presented for these specific genes.
</example>

<example type="include_low_confidence_pqtl_mr">
Paper states: "pQTL-MR analysis using cis instruments identified TFPI as a causal protein for WMH volume (p = 2.3e-6). No direct GWAS association was found at the TFPI locus."
Result: gene_symbol="TFPI", gwas_trait=["WMH"], mendelian_randomization=true, omics_evidence=["pQTL-MR"], confidence=0.5
Reasoning: pQTL-MR with cis instruments provides gene-level evidence, but no direct GWAS association. Flag as MR-exposure/drug target in summary.
</example>

<example type="exclude_background_monogenic">
Paper states: "CADASIL, caused by NOTCH3 mutations, is the most common monogenic form of cSVD. In our GWAS of WMH volume, we identified 20 novel loci."
Do NOT extract NOTCH3: This is a background citation of a known monogenic gene. The paper does not present new NOTCH3 data. Only extract the novel GWAS loci with their specific evidence.
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
