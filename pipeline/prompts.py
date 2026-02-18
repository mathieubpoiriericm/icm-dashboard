"""Prompt definitions for LLM-based gene extraction.

Separates prompt engineering from API call logic so prompts can be
iterated on without touching extraction code.

Supports versioned prompts (v1, v2, v3, v4) for A/B testing during tuning.
"""

from __future__ import annotations

import logging
from typing import Any, Final

logger = logging.getLogger(__name__)

PROMPT_VERSION: Final[str] = "v4"

# ---------------------------------------------------------------------------
# V1 prompts (original baseline)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_V1: Final[str] = (
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

_EXTRACTION_INSTRUCTIONS_V1: Final[str] = """\
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

# ---------------------------------------------------------------------------
# V2 prompts (recalibrated confidence rubric)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_V2: Final[str] = _SYSTEM_PROMPT_V1  # Same system prompt

_EXTRACTION_INSTRUCTIONS_V2: Final[str] = """\
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
IMPORTANT: Use the FULL 0.0-1.0 range. Do NOT cluster scores at 0.50. A gene with genome-wide significant GWAS association for a cSVD phenotype should score at LEAST 0.60 even without additional support. With TWAS and/or colocalization, score 0.70-0.80.

Seven-tier rubric:

- 1.0: Validated causal — established monogenic cause with understood mechanism (e.g., NOTCH3, COL4A1/COL4A2, HTRA1) OR GWAS significance + fine-mapping to credible set of ≤5 variants + functional validation in a cSVD-relevant cell type (brain endothelial, pericyte, VSMC).
- 0.8-0.9: Strong multi-modal convergence — GWAS significance for a cSVD-specific phenotype + eQTL/pQTL colocalization in relevant tissue + at least one supporting line (coding variant, animal model, rare variant burden, or drug target confirmation).
- 0.7-0.8: GWAS significance for a cSVD phenotype + at least ONE supporting line of evidence (TWAS, colocalization, MAGMA gene-based test, coding variant in LD, MTAG cross-trait support). This is the expected tier for novel GWAS loci reported with standard follow-up analyses.
- 0.6-0.7: GWAS genome-wide significance ALONE for a cSVD phenotype with no additional supporting evidence. Also applies to: MTAG-only findings without independent single-trait genome-wide significance.
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

<example type="include_gwas_with_twas">
Paper states: "At the 2q33 locus, rs12476527 reached genome-wide significance for WMH (p = 3.2e-11). TWAS identified NBEAL1 as the most likely causal gene at this locus (TWAS p = 1.4e-6). MAGMA gene-based analysis also implicated NBEAL1 (p = 8.7e-5)."
Result: gene_symbol="NBEAL1", gwas_trait=["WMH"], omics_evidence=["TWAS", "MAGMA"], confidence=0.75
Reasoning: GWAS genome-wide significance + TWAS + MAGMA = multiple supporting lines. Score in the 0.7-0.8 tier.
</example>

<example type="include_gwas_alone">
Paper states: "We identified a novel locus at 10q24 reaching genome-wide significance for SVS (lead SNP rs12345678, p = 4.1e-9). The nearest gene is EXAMPLE1, though no eQTL or functional data are available."
Result: gene_symbol="EXAMPLE1", gwas_trait=["SVS"], confidence=0.65
Reasoning: GWAS genome-wide significance alone without additional support. Score in the 0.6-0.7 tier.
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

# ---------------------------------------------------------------------------
# V3 prompts (positional candidate filtering + confidence hard caps)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_V3: Final[str] = (
    "You are a systematic reviewer specializing in cerebral small vessel disease "
    "(cSVD) genetics. You maintain a curated database of genes with putative "
    "causal links to cSVD, identified through GWAS, MAGMA gene-based tests, "
    "multi-trait GWAS (MTAG), multi-omics studies (TWAS, PWAS, EWAS, proteomics), "
    "Mendelian randomization, colocalization, fine-mapping, proteogenomics/pQTL-MR, "
    "WES/WGS rare variant burden tests, cell-type enrichment analyses, "
    "multi-ancestry GWAS, and functional validation. You are rigorous about "
    "distinguishing causal evidence from mere association or incidental mention. "
    "You carefully distinguish cSVD-specific evidence (small vessel stroke, WMH, "
    "lacunes, PVS, microbleeds) from general stroke or neurodegeneration findings. "
    "You understand that GWAS identifies genomic loci, not individual genes. "
    "A gene's physical proximity to a lead SNP does NOT constitute gene-level "
    "evidence. You require gene-specific statistical tests (gene-based tests, "
    "TWAS, colocalization, fine-mapping, coding variants) to implicate individual "
    "genes at multi-gene loci."
)

_EXTRACTION_INSTRUCTIONS_V3: Final[str] = """\
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

CRITICAL: Physical proximity to a GWAS lead SNP is NOT sufficient for inclusion. A gene must have INDIVIDUAL-LEVEL statistical evidence linking it to a cSVD phenotype. Being listed as a positional candidate gene, or being the nearest gene to a significant SNP, does NOT meet the inclusion criteria unless the paper also reports gene-based test results (MAGMA, TWAS, PWAS, EWAS, colocalization, fine-mapping, or coding variant analysis) for that specific gene.
</inclusion_criteria>

<extraction_strategy>
When analyzing the paper:
1. Identify all passages that mention specific genes in the context of cSVD causality.
2. Verify that each gene was tested in a cSVD-specific analysis (SVS, WMH, lacunes, PVS, microbleeds, or another cSVD phenotype) — not just general stroke, cardioembolic stroke, or large-artery stroke.
3. Distinguish genes with individual statistical evidence (GWAS hit, gene-based significance, MR result) from genes mentioned only as positional candidates at a GWAS locus or as members of enriched pathways. Positional candidacy means the gene is listed because it is near the lead SNP but has NO gene-specific test result. Do NOT extract positional-only candidates. Only extract pathway-only genes if the paper explicitly discusses their individual role.
4. For well-known monogenic cSVD genes (NOTCH3, COL4A1, COL4A2, HTRA1, TREX1, GLA), only extract if the paper presents NEW data — new statistical association, new functional evidence, or new population-level variant analysis. Do NOT extract these genes from background sentences like "CADASIL is caused by NOTCH3 mutations."
5. At multi-gene loci, ONLY extract genes for which the paper reports individual gene-level evidence (gene-based p-value, TWAS/PWAS/EWAS result, coding variant, colocalization, or fine-mapping). Do NOT extract all positional candidates at a locus simply because the locus is significant.
6. Parse for negative results ("did not support," "no association," "failed to replicate"). If a gene was tested and found not associated, assign confidence 0 and note the negative result.
7. Distinguish MR-exposure genes (e.g., "genetically proxied ACE inhibition reduces WMH") from genes with direct cSVD genetic association. Flag MR-only evidence in the causal_evidence_summary.
8. Map animal model gene nomenclature to human HGNC symbols (e.g., mouse Trim47 → TRIM47, zebrafish col4a1 → COL4A1).
9. For PVS (perivascular space) GWAS studies: PVS GWAS loci often contain many genes, and papers commonly list all positional candidates in supplementary tables. Apply extra scrutiny: only extract genes with gene-based test results or functional follow-up, not genes that only appear in locus/positional candidate tables.
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
IMPORTANT: Use the FULL 0.0-1.0 range. Do NOT cluster scores at 0.50. A gene with genome-wide significant GWAS association for a cSVD phenotype should score at LEAST 0.60 even without additional support. With TWAS and/or colocalization, score 0.70-0.80.

IMPORTANT: Before assigning any confidence score above 0.50, verify that the gene has at least ONE of: (a) genome-wide significant gene-based test, (b) significant TWAS/PWAS/EWAS, (c) significant colocalization/SMR, (d) fine-mapping to credible set, (e) coding variant in LD, (f) functional validation. Without any of these, the maximum score is 0.30.

Positional candidate penalty: If the only basis for a gene is its position at a GWAS locus (listed in a locus table, nearest gene), apply a hard cap of 0.20 regardless of the locus's p-value.

Eight-tier rubric:

- 1.0: Validated causal — established monogenic cause with understood mechanism (e.g., NOTCH3, COL4A1/COL4A2, HTRA1) OR GWAS significance + fine-mapping to credible set of ≤5 variants + functional validation in a cSVD-relevant cell type (brain endothelial, pericyte, VSMC).
- 0.8-0.9: Strong multi-modal convergence — GWAS significance for a cSVD-specific phenotype + eQTL/pQTL colocalization in relevant tissue + at least one supporting line (coding variant, animal model, rare variant burden, or drug target confirmation).
- 0.7-0.8: GWAS significance for a cSVD phenotype + at least ONE supporting line of evidence (TWAS, colocalization, MAGMA gene-based test, coding variant in LD, MTAG cross-trait support). This is the expected tier for novel GWAS loci reported with standard follow-up analyses.
- 0.6-0.7: The gene ITSELF has individual genome-wide significance (gene-based test, TWAS, sole gene at a locus) for a cSVD phenotype with no additional supporting evidence. Also applies to: MTAG-only findings without independent single-trait genome-wide significance. NOTE: being AT a significant locus is NOT the same as the gene itself having significance — positional candidates do NOT qualify for this tier.
- 0.4-0.5: Indirect or suggestive — pathway analysis gene only (enriched gene set member, no individual significance); pQTL-MR evidence without direct genetic association; general stroke GWAS gene without cSVD-specific evidence; or animal model findings without human genetic support. pQTL-MR with cis instruments scores at the high end (0.5).
- 0.2-0.3: Weak or contextual — genetic correlation only; unreplicated candidate gene study; pre-GWAS era association; protein interaction network member without direct evidence.
- 0.1-0.2: Positional candidate only — gene is listed at a GWAS locus because of physical proximity to the lead SNP, but has NO individual gene-level statistical test. DO NOT assign scores of 0.4 or higher to positional-only candidates.
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
NOTE: FOXF2 scores 0.9 here because THIS paper presents new functional data (animal model, multi-omic analysis, therapeutic rescue). A different paper that merely cites FOXF2 as a prior GWAS finding without presenting new data should NOT extract FOXF2 — that would be a background citation (confidence 0.0).
</example>

<example type="include_gwas_with_twas">
Paper states: "At the 2q33 locus, rs12476527 reached genome-wide significance for WMH (p = 3.2e-11). TWAS identified NBEAL1 as the most likely causal gene at this locus (TWAS p = 1.4e-6). MAGMA gene-based analysis also implicated NBEAL1 (p = 8.7e-5)."
Result: gene_symbol="NBEAL1", gwas_trait=["WMH"], omics_evidence=["TWAS", "MAGMA"], confidence=0.75
Reasoning: GWAS genome-wide significance + TWAS + MAGMA = multiple supporting lines. Score in the 0.7-0.8 tier.
</example>

<example type="include_gwas_alone">
Paper states: "We identified a novel locus at 10q24 reaching genome-wide significance for SVS (lead SNP rs12345678, p = 4.1e-9). The nearest gene is EXAMPLE1, though no eQTL or functional data are available."
Result: gene_symbol="EXAMPLE1", gwas_trait=["SVS"], confidence=0.65
Reasoning: GWAS genome-wide significance alone without additional support. Score in the 0.6-0.7 tier.
</example>

<example type="exclude_positional_candidate">
Paper states: "At the WM-PVS locus on chromosome 1q25, we identified 8 positional candidate genes: LAMC1, EFEMP1, LPAR1, ITGB5, RGL1, CACNA1E, NOS1AP, and KCNT2. TWAS analysis identified ITGB5 as the only gene with significant brain expression association (p = 3.1e-7)."
Extract ONLY ITGB5 (with TWAS evidence). Do NOT extract LAMC1, EFEMP1, LPAR1, RGL1, CACNA1E, NOS1AP, or KCNT2 — these are positional-only candidates with no individual gene-level evidence. Being listed in a locus table is NOT sufficient.
</example>

<example type="exclude_prior_literature_gene">
Paper states: "FOXF2 has been previously implicated in SVS through GWAS (Chauhan et al., 2019). SLC20A2 is a known cause of familial brain calcification. WNT7A has been linked to blood-brain barrier development."
Do NOT extract FOXF2, SLC20A2, or WNT7A: These are citations of prior literature findings. This paper does not present new statistical or functional data for these genes.
</example>

<example type="include_orf_gene">
Paper states: "TWAS analysis identified C6orf195 as significantly associated with BG-PVS volume in brain cortex (p = 2.8e-6). C6orf195 encodes a protein of unknown function expressed in brain pericytes."
Result: gene_symbol="C6orf195", gwas_trait=["BG-PVS"], omics_evidence=["TWAS"], confidence=0.70
Reasoning: Significant TWAS result provides gene-level evidence. ORF-style gene names (C6orf195, LINC genes, LOC genes) are valid HGNC symbols — do not skip them because the name looks unusual.
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

# ---------------------------------------------------------------------------
# V4 prompts (locus-vs-causal gene fix, EWAS check, MTAG clarification)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_V4: Final[str] = _SYSTEM_PROMPT_V3  # Same system prompt

_EXTRACTION_INSTRUCTIONS_V4: Final[str] = """\
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

CRITICAL: Physical proximity to a GWAS lead SNP is NOT sufficient for inclusion. A gene must have INDIVIDUAL-LEVEL statistical evidence linking it to a cSVD phenotype. Being listed as a positional candidate gene, or being the nearest gene to a significant SNP, does NOT meet the inclusion criteria unless the paper also reports gene-based test results (MAGMA, TWAS, PWAS, EWAS, colocalization, fine-mapping, or coding variant analysis) for that specific gene.
</inclusion_criteria>

<extraction_strategy>
When analyzing the paper:
1. Identify all passages that mention specific genes in the context of cSVD causality.
2. Verify that each gene was tested in a cSVD-specific analysis (SVS, WMH, lacunes, PVS, microbleeds, or another cSVD phenotype) — not just general stroke, cardioembolic stroke, or large-artery stroke.
3. Distinguish genes with individual statistical evidence (GWAS hit, gene-based significance, MR result) from genes mentioned only as positional candidates at a GWAS locus or as members of enriched pathways. Positional candidacy means the gene is listed because it is near the lead SNP but has NO gene-specific test result. Do NOT extract positional-only candidates. Only extract pathway-only genes if the paper explicitly discusses their individual role.
4. For well-known monogenic cSVD genes (NOTCH3, COL4A1, COL4A2, HTRA1, TREX1, GLA), only extract if the paper presents NEW data — new statistical association, new functional evidence, or new population-level variant analysis. Do NOT extract these genes from background sentences like "CADASIL is caused by NOTCH3 mutations."
5. At multi-gene loci, ONLY extract genes for which the paper reports individual gene-level evidence (gene-based p-value, TWAS/PWAS/EWAS result, coding variant, colocalization, or fine-mapping). Do NOT extract all positional candidates at a locus simply because the locus is significant.
6. When a TWAS, colocalization, or fine-mapping analysis identifies a specific causal gene at a GWAS locus, use THAT gene's symbol as the gene_symbol — not the positional locus name or the nearest gene to the lead SNP. For example, if a locus is labeled by LINC01600 in the locus table but TWAS identifies C6orf195 as the causal gene, extract C6orf195.
7. Parse for negative results ("did not support," "no association," "failed to replicate"). If a gene was tested and found not associated, assign confidence 0 and note the negative result.
8. Distinguish MR-exposure genes (e.g., "genetically proxied ACE inhibition reduces WMH") from genes with direct cSVD genetic association. Flag MR-only evidence in the causal_evidence_summary.
9. Map animal model gene nomenclature to human HGNC symbols (e.g., mouse Trim47 → TRIM47, zebrafish col4a1 → COL4A1).
10. For PVS (perivascular space) GWAS studies: PVS GWAS loci often contain many genes, and papers commonly list all positional candidates in supplementary tables. Apply extra scrutiny: only extract genes with gene-based test results or functional follow-up, not genes that only appear in locus/positional candidate tables.
11. Before classifying a gene as positional-only, check ALL omics analyses reported in the paper — including EWAS (epigenome-wide association), methylation analyses, and extreme-phenotype analyses. A gene that appears in an EWAS analysis has individual gene-level evidence and is NOT a positional candidate.
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
IMPORTANT: Use the FULL 0.0-1.0 range. Do NOT cluster scores at 0.50. A gene with genome-wide significant GWAS association for a cSVD phenotype should score at LEAST 0.60 even without additional support. With TWAS and/or colocalization, score 0.70-0.80.

IMPORTANT: Before assigning any confidence score above 0.50, verify that the gene has at least ONE of: (a) genome-wide significant gene-based test, (b) significant TWAS/PWAS/EWAS, (c) significant colocalization/SMR, (d) fine-mapping to credible set, (e) coding variant in LD, (f) functional validation. Without any of these, the maximum score is 0.30.

Positional candidate penalty: If the only basis for a gene is its position at a GWAS locus (listed in a locus table, nearest gene), apply a hard cap of 0.20 regardless of the locus's p-value.

Eight-tier rubric:

- 1.0: Validated causal — established monogenic cause with understood mechanism (e.g., NOTCH3, COL4A1/COL4A2, HTRA1) OR GWAS significance + fine-mapping to credible set of ≤5 variants + functional validation in a cSVD-relevant cell type (brain endothelial, pericyte, VSMC).
- 0.8-0.9: Strong multi-modal convergence — GWAS significance for a cSVD-specific phenotype + eQTL/pQTL colocalization in relevant tissue + at least one supporting line (coding variant, animal model, rare variant burden, or drug target confirmation).
- 0.7-0.8: GWAS significance for a cSVD phenotype + at least ONE supporting line of evidence (TWAS, colocalization, MAGMA gene-based test, coding variant in LD, MTAG cross-trait support). This is the expected tier for novel GWAS loci reported with standard follow-up analyses.
- 0.6-0.7: The gene ITSELF has individual genome-wide significance (gene-based test, TWAS, sole gene at a locus) for a cSVD phenotype with no additional supporting evidence. Also applies to: MTAG-only findings without independent single-trait genome-wide significance. MTAG (multi-trait GWAS) reaching genome-wide significance IS gene-level evidence when the gene is the nearest gene at the MTAG-specific locus — do not treat these as positional candidates. NOTE: being AT a significant locus is NOT the same as the gene itself having significance — positional candidates do NOT qualify for this tier.
- 0.4-0.5: Indirect or suggestive — pathway analysis gene only (enriched gene set member, no individual significance); pQTL-MR evidence without direct genetic association; general stroke GWAS gene without cSVD-specific evidence; or animal model findings without human genetic support. pQTL-MR with cis instruments scores at the high end (0.5).
- 0.2-0.3: Weak or contextual — genetic correlation only; unreplicated candidate gene study; pre-GWAS era association; protein interaction network member without direct evidence.
- 0.1-0.2: Positional candidate only — gene is listed at a GWAS locus because of physical proximity to the lead SNP, but has NO individual gene-level statistical test. DO NOT assign scores of 0.4 or higher to positional-only candidates.
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
NOTE: FOXF2 scores 0.9 here because THIS paper presents new functional data (animal model, multi-omic analysis, therapeutic rescue). A different paper that merely cites FOXF2 as a prior GWAS finding without presenting new data should NOT extract FOXF2 — that would be a background citation (confidence 0.0).
</example>

<example type="include_gwas_with_twas">
Paper states: "At the 2q33 locus, rs12476527 reached genome-wide significance for WMH (p = 3.2e-11). TWAS identified NBEAL1 as the most likely causal gene at this locus (TWAS p = 1.4e-6). MAGMA gene-based analysis also implicated NBEAL1 (p = 8.7e-5)."
Result: gene_symbol="NBEAL1", gwas_trait=["WMH"], omics_evidence=["TWAS", "MAGMA"], confidence=0.75
Reasoning: GWAS genome-wide significance + TWAS + MAGMA = multiple supporting lines. Score in the 0.7-0.8 tier.
</example>

<example type="include_gwas_alone">
Paper states: "We identified a novel locus at 10q24 reaching genome-wide significance for SVS (lead SNP rs12345678, p = 4.1e-9). The nearest gene is EXAMPLE1, though no eQTL or functional data are available."
Result: gene_symbol="EXAMPLE1", gwas_trait=["SVS"], confidence=0.65
Reasoning: GWAS genome-wide significance alone without additional support. Score in the 0.6-0.7 tier.
</example>

<example type="include_twas_causal_gene">
Paper states: "At the WM-PVS locus 6p25.2, the lead SNP is near LINC01600. TWAS analysis identified C6orf195 as transcriptome-wide significant (p = 2.8e-6) with colocalization PP4 > 0.75."
Result: gene_symbol="C6orf195", gwas_trait=["WM-PVS"], omics_evidence=["TWAS", "colocalization"], confidence=0.75
Do NOT extract LINC01600 — it is the positional locus label, not the causal gene identified by TWAS/colocalization.
</example>

<example type="exclude_positional_candidate">
Paper states: "At the WM-PVS locus on chromosome 1q25, we identified 8 positional candidate genes: LAMC1, EFEMP1, LPAR1, ITGB5, RGL1, CACNA1E, NOS1AP, and KCNT2. TWAS analysis identified ITGB5 as the only gene with significant brain expression association (p = 3.1e-7)."
Extract ONLY ITGB5 (with TWAS evidence). Do NOT extract LAMC1, EFEMP1, LPAR1, RGL1, CACNA1E, NOS1AP, or KCNT2 — these are positional-only candidates with no individual gene-level evidence. Being listed in a locus table is NOT sufficient.
</example>

<example type="exclude_prior_literature_gene">
Paper states: "FOXF2 has been previously implicated in SVS through GWAS (Chauhan et al., 2019). SLC20A2 is a known cause of familial brain calcification. WNT7A has been linked to blood-brain barrier development."
Do NOT extract FOXF2, SLC20A2, or WNT7A: These are citations of prior literature findings. This paper does not present new statistical or functional data for these genes.
</example>

<example type="include_orf_gene">
Paper states: "TWAS analysis identified C6orf195 as significantly associated with BG-PVS volume in brain cortex (p = 2.8e-6). C6orf195 encodes a protein of unknown function expressed in brain pericytes."
Result: gene_symbol="C6orf195", gwas_trait=["BG-PVS"], omics_evidence=["TWAS"], confidence=0.70
Reasoning: Significant TWAS result provides gene-level evidence. ORF-style gene names (C6orf195, LINC genes, LOC genes) are valid HGNC symbols — do not skip them because the name looks unusual.
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

# ---------------------------------------------------------------------------
# Version dispatch
# ---------------------------------------------------------------------------

_PROMPTS: Final[dict[str, tuple[str, str]]] = {
    "v1": (_SYSTEM_PROMPT_V1, _EXTRACTION_INSTRUCTIONS_V1),
    "v2": (_SYSTEM_PROMPT_V2, _EXTRACTION_INSTRUCTIONS_V2),
    "v3": (_SYSTEM_PROMPT_V3, _EXTRACTION_INSTRUCTIONS_V3),
    "v4": (_SYSTEM_PROMPT_V4, _EXTRACTION_INSTRUCTIONS_V4),
}

# Public aliases for backwards compatibility (point to current default)
SYSTEM_PROMPT: Final[str] = _SYSTEM_PROMPT_V4
EXTRACTION_INSTRUCTIONS: Final[str] = _EXTRACTION_INSTRUCTIONS_V4


def build_extraction_messages(
    paper_text: str,
    pmid: str,
    max_chars: int,
    prompt_version: str = "v4",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build system blocks and messages with cache_control for prompt caching.

    Args:
        paper_text: Full text of the paper.
        pmid: PubMed ID.
        max_chars: Maximum chars for paper text.
        prompt_version: Prompt version to use ("v1", "v2", "v3", or "v4").

    Returns:
        (system_blocks, messages) — ready to pass to ``client.messages.create()``.
        The system prompt and extraction instructions are cached in the system
        blocks (prefix match preserved across calls). The user message contains
        only the paper document and a short extraction query.
    """
    if prompt_version not in _PROMPTS:
        logger.warning(
            f"Unknown prompt version {prompt_version!r}, falling back to 'v4'"
        )
        prompt_version = "v4"

    system_prompt, extraction_instructions = _PROMPTS[prompt_version]

    system_blocks: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        },
        {
            "type": "text",
            "text": extraction_instructions,
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        },
    ]

    # Truncate and log if paper text exceeds max_chars
    if len(paper_text) > max_chars:
        logger.info(
            f"PMID {pmid}: truncating paper text from "
            f"{len(paper_text):,} to {max_chars:,} chars"
        )
        paper_text = paper_text[:max_chars]

    # Escape </document> in paper text to prevent XML injection that
    # would prematurely close the document tag and corrupt the prompt.
    safe_text = paper_text.replace("</document>", "&lt;/document&gt;")

    user_blocks: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                f'<document source="PubMed" pmid="{pmid}">\n'
                f"{safe_text}\n"
                f"</document>\n\n"
                "Extract all genes with putative causal links to cSVD "
                "from the document above."
            ),
        },
    ]

    messages: list[dict[str, Any]] = [{"role": "user", "content": user_blocks}]
    return system_blocks, messages
