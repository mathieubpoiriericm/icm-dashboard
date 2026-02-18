# LLM Tuning Plan: Gene Extraction Pipeline for cSVD Literature Mining

## Context

This project is a Python ETL pipeline that extracts genes of interest from peer-reviewed scientific papers (PDFs) related to **cerebral small vessel disease (cSVD)**. The pipeline uses an LLM for gene extraction with a confidence scoring system to accept or reject candidates. A manually curated **gold standard reference table** exists for validation, along with a **comparison table tracking rejected genes and false negatives**.

The goal of this plan is to systematically improve extraction accuracy (precision and recall) through threshold calibration, prompt engineering, gene normalization, chunking improvements, and optional multi-pass verification.

---

## Phase 1: Error Analysis Module

**Objective:** Categorize all errors from the current pipeline output against the gold standard to identify the dominant failure mode before tuning anything.

### Requirements

- Read the gold standard reference table and the pipeline output table (accepted genes + rejected genes with their confidence scores).
- Classify every discrepancy into one of three categories:
  1. **Threshold false negatives** — the LLM extracted the gene, but the confidence score fell below the acceptance threshold (gene appears in the rejected-genes table AND in the gold standard).
  2. **Miss false negatives** — the gene is in the gold standard but the LLM never extracted it at all (not in accepted or rejected output).
  3. **False positives** — the LLM extracted and accepted a gene that is NOT in the gold standard.
- Output a summary report with counts and percentages per category, plus a detailed table of every misclassified gene with its paper ID, confidence score (if applicable), and error category.
- Persist the error analysis results (e.g., CSV or database table) so they can be compared across tuning iterations.

### Implementation Notes

- Account for gene name synonyms/aliases when matching pipeline output to the gold standard. A gene extracted as `NOTCH3` and listed in the gold standard as `NOTCH3` is a match, but so is `Notch3` (case-insensitive). Consider also HGNC alias matching (see Phase 4).
- If the gold standard uses a different identifier scheme (e.g., Ensembl IDs vs. HGNC symbols), build a mapping layer first.

---

## Phase 2: Confidence Threshold Calibration

**Objective:** Find the optimal confidence score threshold that maximizes the chosen performance metric (F₂ by default, since missing real genes is costlier than including spurious ones).

### Requirements

- Collect all LLM extractions (both accepted and rejected) with their confidence scores across the validation corpus.
- Label each extraction as `1` (true positive — present in gold standard) or `0` (false positive — not in gold standard).
- Compute precision-recall curves and find the threshold that maximizes the F₂ score.
- Also compute and report: F₁, precision, recall, and total gene count at the optimal threshold.
- Produce a precision-recall curve plot saved to disk.
- Output the recommended threshold value.

### Implementation Guidance

```python
import numpy as np
from sklearn.metrics import precision_recall_curve

scores = np.array([...])  # all confidence scores
labels = np.array([...])  # 1 = in gold standard, 0 = not

precisions, recalls, thresholds = precision_recall_curve(labels, scores)

beta = 2  # F-beta weighting — use beta=2 to favor recall
f_beta = (1 + beta**2) * (precisions[:-1] * recalls[:-1]) / (
    (beta**2 * precisions[:-1]) + recalls[:-1] + 1e-8
)
optimal_threshold = thresholds[np.argmax(f_beta)]
```

- Allow the beta value to be configurable (default=2).
- If the score distribution is bimodal or poorly calibrated (most scores clustered near 0 or 100), flag this — it suggests the scoring rubric in the prompt needs rework before threshold tuning is meaningful.

---

## Phase 3: Prompt Engineering Improvements

**Objective:** Rewrite the extraction prompt to improve both recall (fewer misses) and score calibration (scores that actually correlate with correctness).

### 3a. Structured Extraction with Chain-of-Thought

Modify the extraction prompt so the LLM must provide explicit reasoning before assigning a confidence score. The prompt should request:

1. The **exact quote** from the paper where the gene is mentioned.
2. The **claimed relationship** between the gene and cSVD (or the specific phenotype studied).
3. The **evidence type** classification: `genetic_association`, `functional_study`, `animal_model`, `pathway_involvement`, `review_mention_only`, or `other`.
4. A **confidence score (0–100)** based on a rubric the prompt defines.

### 3b. Confidence Scoring Rubric

Include an explicit rubric in the prompt. Example:

```
Score 80-100: The paper presents original data (GWAS, sequencing, functional assay)
              directly linking this gene to cSVD or a cSVD phenotype (WMH, lacunar
              stroke, microbleeds, etc.). Gene name is unambiguous.

Score 50-79:  The paper cites another study's finding linking this gene to cSVD,
              or the gene is implicated through pathway analysis / network analysis
              rather than direct evidence. OR the gene is linked to a related but
              not identical phenotype (e.g., large vessel stroke).

Score 20-49:  The gene is mentioned in the context of cSVD but the relationship
              is speculative, tangential, or the gene is part of a large gene list
              without individual discussion.

Score 0-19:   The gene is mentioned but has no stated connection to cSVD, or the
              mention is about a different disease entirely, or the "gene" is
              actually a protein/pathway name that doesn't map to a specific locus.
```

### 3c. Few-Shot Examples

Include 3–5 representative examples in the prompt drawn from the gold standard:

- **Clear true positive** (high confidence, unambiguous gene-cSVD link with original data).
- **Borderline case** (gene mentioned in a pathway context, moderate confidence).
- **True negative** (gene mentioned in the paper but unrelated to cSVD).
- **Tricky alias case** (gene referred to by protein name or older symbol).

Select these examples from the error analysis output (Phase 1) to target the most common failure modes.

### 3d. Output Format

Require structured JSON output from the LLM for easier downstream parsing:

```json
{
  "genes": [
    {
      "symbol": "NOTCH3",
      "quoted_evidence": "Mutations in NOTCH3 are the established cause of CADASIL...",
      "relationship": "causal_mutation",
      "evidence_type": "genetic_association",
      "confidence": 95
    }
  ]
}
```

### Implementation Notes

- Keep the current prompt as a baseline. Create the new prompt as a separate version so results can be A/B compared.
- Store the prompt version identifier alongside each pipeline run's results.

---

## Phase 4: Gene Name Normalization

**Objective:** Map all extracted gene names to canonical HGNC symbols to eliminate false negatives caused by synonyms, aliases, protein names, or formatting differences.

### Requirements

- After LLM extraction and before comparison with the gold standard, normalize every extracted gene name to its official HGNC symbol.
- Use the `mygene` Python package (MyGene.info API) or a local HGNC lookup table.
- Handle common issues:
  - Case variations: `notch3` → `NOTCH3`
  - Protein names: `collagen IV` → `COL4A1` / `COL4A2`
  - Old symbols: historical aliases that HGNC has updated
  - Species prefixes: strip `h` or `Hs` prefixes if present
- Log every normalization mapping applied (original → canonical) for auditability.
- If a name cannot be resolved, flag it for manual review rather than silently dropping it.

### Implementation Guidance

```python
import mygene

mg = mygene.MyGeneInfo()

def normalize_gene(name: str) -> dict:
    """Return canonical symbol and metadata, or flag as unresolved."""
    results = mg.query(
        name,
        scopes="symbol,alias,name,ensembl.gene",
        species="human",
        fields="symbol,alias,name,ensemblgene",
        size=3
    )
    if results.get("hits"):
        top = results["hits"][0]
        return {
            "input": name,
            "canonical_symbol": top.get("symbol"),
            "match_score": top.get("_score"),
            "resolved": True
        }
    return {"input": name, "canonical_symbol": None, "resolved": False}
```

- Also normalize the gold standard table itself using the same logic so both sides of the comparison use HGNC symbols.
- Cache API results locally (SQLite or a dict persisted to JSON) to avoid repeated lookups and rate limiting.

---

## Phase 5: PDF Chunking Improvements

**Objective:** Reduce false negatives caused by genes being lost at chunk boundaries or buried in sections the LLM never sees.

### Requirements

- **Chunk overlap:** Ensure at least 15% overlap between consecutive chunks so boundary genes are captured.
- **Section-aware splitting:** If the PDF parser provides section headers, prioritize sending these sections to the LLM:
  1. Abstract
  2. Results
  3. Discussion
  4. Tables and table captions
  5. Methods (lower priority but useful for gene lists)
- **Two-pass approach (optional but recommended):**
  - **Pass 1 (broad scan):** Send the full abstract + Results + Discussion with a permissive threshold to generate a candidate gene list.
  - **Pass 2 (targeted verification):** For each candidate gene, search the full text for all mentions and send the relevant paragraphs back to the LLM for confirmation (see Phase 6).

### Implementation Notes

- Track which section each extraction came from. This helps identify whether most misses come from tables, supplementary material, or specific sections.
- If using a library like `pymupdf` / `pdfplumber`, extract tables separately and convert them to a text/markdown representation before sending to the LLM — tabular data in raw PDF text extraction is often garbled.

---

## Phase 6: Multi-Pass Verification (Optional)

**Objective:** Use a second LLM call with a verification-focused prompt to confirm or reject candidates from the extraction pass, reducing false positives without losing recall.

### Requirements

- After the extraction pass (Phase 3), take all candidates above a **low** threshold (e.g., 0.3 × the optimal threshold from Phase 2).
- For each candidate gene, construct a verification prompt that:
  - Names the specific gene.
  - Provides the relevant text passage(s) from the paper.
  - Asks a focused yes/no question: "Does this paper provide evidence that [GENE] is involved in cerebral small vessel disease or a cSVD-related phenotype?"
  - Requests a confidence score and a one-sentence justification.
- Accept the gene only if the verification pass also confirms it above a separate verification threshold.

### Implementation Guidance

```python
async def verify_gene(gene_symbol: str, text_passages: list[str], llm_client) -> dict:
    prompt = f"""
    A gene extraction system identified {gene_symbol} as potentially linked
    to cerebral small vessel disease (cSVD) based on this paper.

    Relevant passages:
    {chr(10).join(text_passages)}

    Question: Does this paper provide evidence that {gene_symbol} is involved
    in cSVD or a cSVD-related phenotype (white matter hyperintensities,
    lacunar infarcts, cerebral microbleeds, CADASIL, etc.)?

    Respond in JSON:
    {{"confirmed": true/false, "confidence": 0-100, "justification": "..."}}
    """
    return await llm_client.complete(prompt)
```

### Implementation Notes

- This doubles (or more) the LLM API cost per paper. Only implement if Phase 1 error analysis shows a significant false positive problem that prompt improvements alone haven't solved.
- The verification prompt should be simpler and more focused than the extraction prompt — the LLM performs better at verification than open-ended extraction.

---

## Phase 7: Iteration Tracking and Regression Testing

**Objective:** Track every tuning iteration so improvements can be measured and regressions caught.

### Requirements

- Create a **runs table** (database or CSV) that records for each pipeline run:
  - Run ID / timestamp
  - Prompt version identifier
  - Confidence threshold used
  - Beta value used for F-beta optimization
  - Whether gene normalization was applied
  - Whether verification pass was enabled
  - Chunking strategy version
  - Metrics: precision, recall, F₁, F₂, total genes extracted, true positives, false positives, false negatives (threshold), false negatives (miss)
- After each tuning change, re-run the pipeline on the full validation corpus and append a new row.
- Flag any run where recall drops by more than 2 percentage points compared to the previous best — this is a regression.
- Generate a comparison summary (table or plot) showing metric trends across runs.

### Implementation Notes

- The validation corpus should remain fixed during tuning. Do not add new papers to it mid-optimization — this confounds the comparison.
- If the gold standard is updated (e.g., to fix errors found during analysis), note this in the runs table and re-baseline all prior metrics.

---

## Execution Order

Run these phases in order. Each phase depends on insights from the previous one:

1. **Phase 1 (Error Analysis)** — understand where the current pipeline fails.
2. **Phase 2 (Threshold Calibration)** — quick win if threshold FNs dominate.
3. **Phase 3 (Prompt Engineering)** — usually the highest-impact change.
4. **Phase 4 (Gene Normalization)** — catches synonym-based mismatches.
5. **Phase 5 (Chunking)** — addresses miss-type false negatives.
6. **Phase 6 (Verification Pass)** — optional, for stubborn false positive problems.
7. **Phase 7 (Tracking)** — should actually be set up before Phase 2 so all iterations are tracked from the start. Listed last for logical flow but implement the tracking infrastructure early.

After each phase, re-run error analysis (Phase 1) to see whether the dominant failure mode has shifted before proceeding to the next phase.
