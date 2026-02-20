"""Compare pipeline gene extraction output against a gold-standard reference table.

Usage:
    python scripts/validate_pipeline.py logs/pipeline_report_<ts>.json
    python scripts/validate_pipeline.py report.json \\
        --reference data/test_data/gold_standard_v2.csv
    python scripts/validate_pipeline.py report.json --output-dir results/
    python scripts/validate_pipeline.py report.json --fulltext-only
    python scripts/validate_pipeline.py report.json --local-pdfs
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

# ---------------------------------------------------------------------------
# 1. Constants & Enums
# ---------------------------------------------------------------------------

NONE_FOUND_SENTINELS: set[str] = {"(none found)", "none found", "n/a", "na", ""}
REFERENCE_NEEDED_SENTINELS: set[str] = {"(reference needed)", "reference needed"}

DEFAULT_REFERENCE_PATH = Path("data/test_data/gold_standard_v2.csv")

_PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent


class MatchStatus(Enum):
    MATCH = "match"
    PARTIAL = "partial"
    MISMATCH = "mismatch"
    MISSING_REF = "missing_ref"
    MISSING_PIPELINE = "missing_pipeline"
    BOTH_EMPTY = "both_empty"
    SKIPPED = "skipped"


STATUS_COLORS: dict[MatchStatus, str] = {
    MatchStatus.MATCH: "#c8e6c9",
    MatchStatus.PARTIAL: "#fff9c4",
    MatchStatus.MISMATCH: "#ffcdd2",
    MatchStatus.MISSING_REF: "#e0e0e0",
    MatchStatus.MISSING_PIPELINE: "#e0e0e0",
    MatchStatus.BOTH_EMPTY: "#c8e6c9",
    MatchStatus.SKIPPED: "#e0e0e0",
}

# Alias map: reference name → set of pipeline gene symbols that should merge
GENE_ALIAS_MAP: dict[str, set[str]] = {
    "COL4A1/2": {"COL4A1", "COL4A2"},
}

# Reverse lookup: pipeline symbol → reference name
_REVERSE_GENE_ALIASES: dict[str, str] = {}
for _ref_name, _pipe_names in GENE_ALIAS_MAP.items():
    for _pn in _pipe_names:
        _REVERSE_GENE_ALIASES[_pn.upper()] = _ref_name.upper()

# NCBI gene symbol aliases: old/alternative names → current official symbol.
# Handles gene renames by NCBI (e.g., C6orf195 was renamed to LINC01600).
NCBI_GENE_ALIASES: dict[str, str] = {
    "C6ORF195": "LINC01600",
}

# Trait aliases: normalize alternative names to canonical form
TRAIT_ALIASES: dict[str, str] = {
    "cmb": "cerebral-microbleeds",
    "noddi": "icvf",
}

# ---------------------------------------------------------------------------
# 2. Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class AggregatedGene:
    symbol: str
    gwas_traits: set[str] = field(default_factory=set)
    mr: bool = False
    omics: set[str] = field(default_factory=set)
    pmids: set[str] = field(default_factory=set)
    pmids_reference_needed: bool = False
    confidences: list[float] = field(default_factory=list)


@dataclass
class RejectedGeneInfo:
    """A gene that was rejected during pipeline validation."""

    symbol: str
    protein_name: str
    pmid: str
    confidence: float
    reasons: list[str]


@dataclass
class FieldComparison:
    field_name: str
    status: MatchStatus
    ref_value: str
    pipe_value: str
    score: float


@dataclass
class GeneComparison:
    gene: str
    detection_status: MatchStatus
    fields: list[FieldComparison] = field(default_factory=list)
    gene_score: float = 0.0
    mean_confidence: float = 0.0


@dataclass
class ValidationScores:
    # Gene detection
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0

    # Per-field aggregates
    mean_gwas_jaccard: float = 0.0
    mr_accuracy: float = 0.0
    mean_omics_jaccard: float = 0.0
    mean_pmid_recall: float = 0.0

    # Overall
    mean_gene_score: float = 0.0
    composite: float = 0.0


# ---------------------------------------------------------------------------
# 3. Normalization functions
# ---------------------------------------------------------------------------


def normalize_gene_symbol(symbol: str) -> str:
    upper = symbol.strip().upper()
    return NCBI_GENE_ALIASES.get(upper, upper)


def _is_sentinel(value: str, sentinels: set[str]) -> bool:
    return value.strip().lower() in sentinels


def parse_comma_set(value: str, sentinels: set[str] | None = None) -> set[str]:
    """Parse a comma-separated string into a normalized set."""
    if sentinels and _is_sentinel(value, sentinels):
        return set()
    parts = re.split(r",\s*", value.strip())
    return {p.strip().lower() for p in parts if p.strip()}


def normalize_trait(trait: str) -> str:
    """Normalize a single GWAS trait string."""
    t = trait.strip().lower()
    return TRAIT_ALIASES.get(t, t)


def normalize_traits(raw: str) -> set[str]:
    """Parse and normalize GWAS traits from a comma-separated string."""
    if _is_sentinel(raw, NONE_FOUND_SENTINELS):
        return set()
    parts = re.split(r",\s*", raw.strip())
    return {normalize_trait(p) for p in parts if p.strip()}


def normalize_omics_base(entry: str) -> str:
    """Extract base omics type, stripping tissue annotation after ';'.

    Examples:
        'TWAS;brain frontal cortex' → 'twas'
        'TWAS;' → 'twas'
        'proteomics' → 'proteomics'
    """
    base = entry.split(";")[0].strip().lower()
    return base if base else ""


def normalize_omics_set(raw: str) -> set[str]:
    """Parse omics evidence string into a set of base types."""
    if _is_sentinel(raw, NONE_FOUND_SENTINELS):
        return set()
    parts = re.split(r",\s*", raw.strip())
    result: set[str] = set()
    for p in parts:
        base = normalize_omics_base(p)
        if base:
            result.add(base)
    return result


def parse_pmids(raw: str) -> tuple[set[str], bool]:
    """Parse PMID string. Returns (pmid_set, is_reference_needed)."""
    if _is_sentinel(raw, REFERENCE_NEEDED_SENTINELS):
        return set(), True
    if _is_sentinel(raw, NONE_FOUND_SENTINELS):
        return set(), False
    parts = re.split(r",\s*", raw.strip())
    return {p.strip() for p in parts if p.strip()}, False


def parse_bool(raw: str) -> bool:
    return raw.strip().lower() in ("yes", "true", "1")


def is_non_numeric_pmid(pmid: str) -> bool:
    """Detect non-numeric PMIDs (e.g. filenames from --local-pdfs mode)."""
    return not pmid.strip().isdigit()


# ---------------------------------------------------------------------------
# 4. Parsers
# ---------------------------------------------------------------------------


def parse_reference_csv(path: Path) -> dict[str, AggregatedGene]:
    """Parse gold-standard reference CSV into a dict keyed by upper-case gene symbol."""
    genes: dict[str, AggregatedGene] = {}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            symbol = normalize_gene_symbol(row["gene"])
            traits = normalize_traits(row["gwas_trait"])
            mr = parse_bool(row["mr"])
            omics = normalize_omics_set(row["omics"])
            pmids, ref_needed = parse_pmids(row["pmid"])

            genes[symbol] = AggregatedGene(
                symbol=symbol,
                gwas_traits=traits,
                mr=mr,
                omics=omics,
                pmids=pmids,
                pmids_reference_needed=ref_needed,
            )
    return genes


def parse_pipeline_json(
    path: Path, *, fulltext_only: bool = False
) -> tuple[dict[str, AggregatedGene], set[str], list[RejectedGeneInfo]]:
    """Parse pipeline report JSON, aggregating same gene across papers.

    Returns:
        (aggregated_genes, fulltext_pmids, rejected_genes) — the second
        element contains PMIDs from papers where ``fulltext == True``,
        the third contains genes rejected during pipeline validation.
    """
    with open(path, encoding="utf-8") as f:
        report = json.load(f)

    aggregated: dict[str, AggregatedGene] = {}
    fulltext_pmids: set[str] = set()
    rejected_genes: list[RejectedGeneInfo] = []

    for paper in report.get("papers_detail", []):
        is_fulltext = paper.get("fulltext", False)
        paper_pmid = str(paper.get("pmid", ""))

        if is_fulltext and paper_pmid:
            fulltext_pmids.add(paper_pmid)

        if fulltext_only and not is_fulltext:
            continue

        for gene_data in paper.get("genes", []):
            symbol = normalize_gene_symbol(gene_data.get("gene_symbol", ""))
            if not symbol:
                continue

            if symbol not in aggregated:
                aggregated[symbol] = AggregatedGene(symbol=symbol)

            ag = aggregated[symbol]

            # GWAS traits: union
            for trait in gene_data.get("gwas_trait", []):
                normalized = normalize_trait(trait)
                if normalized:
                    ag.gwas_traits.add(normalized)

            # MR: OR across papers
            if gene_data.get("mendelian_randomization", False):
                ag.mr = True

            # Omics: union of base types
            for omics_entry in gene_data.get("omics_evidence", []):
                base = normalize_omics_base(omics_entry)
                if base:
                    ag.omics.add(base)

            # PMIDs: collect paper PMID
            if paper_pmid:
                ag.pmids.add(paper_pmid)

            # Confidence: collect
            conf = gene_data.get("confidence")
            if conf is not None:
                ag.confidences.append(float(conf))

        # Collect rejected genes
        for rg in paper.get("rejected_genes", []):
            gene_data = rg.get("gene", {})
            symbol = gene_data.get("gene_symbol", "")
            if symbol:
                rejected_genes.append(
                    RejectedGeneInfo(
                        symbol=normalize_gene_symbol(symbol),
                        protein_name=gene_data.get("protein_name") or "",
                        pmid=str(gene_data.get("pmid", paper_pmid)),
                        confidence=float(gene_data.get("confidence", 0)),
                        reasons=rg.get("reasons", []),
                    )
                )

    return aggregated, fulltext_pmids, rejected_genes


def filter_reference_for_fulltext(
    ref_genes: dict[str, AggregatedGene],
    fulltext_pmids: set[str],
) -> dict[str, AggregatedGene]:
    """Keep only reference genes linked to at least one fulltext PMID.

    Genes with ``pmids_reference_needed == True`` are excluded because their
    provenance is unknown and cannot be verified in fulltext-only mode.
    """
    return {
        symbol: gene
        for symbol, gene in ref_genes.items()
        if not gene.pmids_reference_needed and gene.pmids & fulltext_pmids
    }


def filter_reference_for_single_pmid(
    ref_genes: dict[str, AggregatedGene],
    single_pmid: str,
) -> dict[str, AggregatedGene]:
    """Keep only reference genes whose sole PMID is *single_pmid*.

    Excludes genes with ``pmids_reference_needed`` and genes with
    multiple PMIDs, since their evidence spans papers the pipeline
    did not process.
    """
    return {
        symbol: gene
        for symbol, gene in ref_genes.items()
        if not gene.pmids_reference_needed and gene.pmids == {single_pmid}
    }


# ---------------------------------------------------------------------------
# 5. Comparison engine
# ---------------------------------------------------------------------------


def jaccard_index(a: set[str], b: set[str]) -> float:
    """Compute Jaccard index. Returns 1.0 if both empty."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def set_recall(reference: set[str], pipeline: set[str]) -> float:
    """Compute recall: what fraction of reference items are in pipeline."""
    if not reference:
        return 1.0
    return len(reference & pipeline) / len(reference)


def compare_sets(
    ref: set[str], pipe: set[str], field_name: str, *, use_jaccard: bool = True
) -> FieldComparison:
    """Compare two sets and return a FieldComparison."""
    ref_str = ", ".join(sorted(ref)) if ref else "(empty)"
    pipe_str = ", ".join(sorted(pipe)) if pipe else "(empty)"

    if not ref and not pipe:
        return FieldComparison(
            field_name, MatchStatus.BOTH_EMPTY, ref_str, pipe_str, 1.0
        )

    score = jaccard_index(ref, pipe) if use_jaccard else set_recall(ref, pipe)

    if ref == pipe:
        status = MatchStatus.MATCH
    elif ref & pipe:
        status = MatchStatus.PARTIAL
    elif not ref:
        status = MatchStatus.MISSING_REF
    elif not pipe:
        status = MatchStatus.MISSING_PIPELINE
    else:
        status = MatchStatus.MISMATCH

    return FieldComparison(field_name, status, ref_str, pipe_str, score)


def compare_boolean(ref: bool, pipe: bool, field_name: str) -> FieldComparison:
    """Compare two boolean values."""
    ref_str = "Yes" if ref else "No"
    pipe_str = "Yes" if pipe else "No"
    match = ref == pipe
    status = MatchStatus.MATCH if match else MatchStatus.MISMATCH
    return FieldComparison(field_name, status, ref_str, pipe_str, 1.0 if match else 0.0)


def compare_pmids(
    ref_gene: AggregatedGene, pipe_gene: AggregatedGene, has_non_numeric: bool
) -> FieldComparison:
    """Compare PMID sets with special handling for reference-needed
    and non-numeric PMIDs."""
    if ref_gene.pmids_reference_needed or has_non_numeric:
        ref_label = (
            "(reference needed)" if ref_gene.pmids_reference_needed else "(non-numeric)"
        )
        pipe_label = (
            ", ".join(sorted(pipe_gene.pmids)) if pipe_gene.pmids else "(empty)"
        )
        return FieldComparison(
            "PMIDs",
            MatchStatus.SKIPPED,
            ref_label,
            pipe_label,
            0.0,
        )

    ref_set = ref_gene.pmids
    pipe_set = pipe_gene.pmids
    ref_str = ", ".join(sorted(ref_set)) if ref_set else "(empty)"
    pipe_str = ", ".join(sorted(pipe_set)) if pipe_set else "(empty)"

    if not ref_set and not pipe_set:
        return FieldComparison("PMIDs", MatchStatus.BOTH_EMPTY, ref_str, pipe_str, 1.0)

    score = set_recall(ref_set, pipe_set)

    if ref_set == pipe_set:
        status = MatchStatus.MATCH
    elif ref_set & pipe_set:
        status = MatchStatus.PARTIAL
    elif not pipe_set:
        status = MatchStatus.MISSING_PIPELINE
    else:
        status = MatchStatus.MISMATCH

    return FieldComparison("PMIDs", status, ref_str, pipe_str, score)


def remap_pipeline_genes(
    pipeline_genes: dict[str, AggregatedGene],
) -> dict[str, AggregatedGene]:
    """Merge pipeline genes that map to the same reference alias.

    E.g., COL4A1 + COL4A2 in pipeline → COL4A1/2 in output.
    """
    remapped: dict[str, AggregatedGene] = {}
    consumed: set[str] = set()

    for symbol, gene in pipeline_genes.items():
        ref_name = _REVERSE_GENE_ALIASES.get(symbol)
        if ref_name:
            consumed.add(symbol)
            if ref_name not in remapped:
                remapped[ref_name] = AggregatedGene(symbol=ref_name)
            target = remapped[ref_name]
            target.gwas_traits |= gene.gwas_traits
            target.mr = target.mr or gene.mr
            target.omics |= gene.omics
            target.pmids |= gene.pmids
            target.confidences.extend(gene.confidences)

    # Add non-alias genes as-is
    for symbol, gene in pipeline_genes.items():
        if symbol not in consumed:
            remapped[symbol] = gene

    return remapped


def compare_all(
    ref_genes: dict[str, AggregatedGene],
    pipe_genes: dict[str, AggregatedGene],
) -> tuple[list[GeneComparison], list[AggregatedGene], list[AggregatedGene]]:
    """Compare reference and pipeline genes.

    Returns:
        (matched_comparisons, false_negatives, false_positives)
    """
    pipe_remapped = remap_pipeline_genes(pipe_genes)

    # Detect non-numeric PMIDs (local-pdf mode)
    has_non_numeric = any(
        is_non_numeric_pmid(pmid) for g in pipe_remapped.values() for pmid in g.pmids
    )

    all_ref_keys = set(ref_genes.keys())
    all_pipe_keys = set(pipe_remapped.keys())

    matched_keys = all_ref_keys & all_pipe_keys
    fn_keys = all_ref_keys - all_pipe_keys
    fp_keys = all_pipe_keys - all_ref_keys

    comparisons: list[GeneComparison] = []
    for key in sorted(matched_keys):
        ref = ref_genes[key]
        pipe = pipe_remapped[key]

        fields: list[FieldComparison] = [
            compare_sets(ref.gwas_traits, pipe.gwas_traits, "GWAS Traits"),
            compare_boolean(ref.mr, pipe.mr, "MR"),
            compare_sets(ref.omics, pipe.omics, "Omics"),
            compare_pmids(ref, pipe, has_non_numeric),
        ]

        mean_conf = (
            sum(pipe.confidences) / len(pipe.confidences) if pipe.confidences else 0.0
        )

        comp = GeneComparison(
            gene=key,
            detection_status=MatchStatus.MATCH,
            fields=fields,
            mean_confidence=mean_conf,
        )
        comparisons.append(comp)

    false_negatives = [ref_genes[k] for k in sorted(fn_keys)]
    false_positives = [pipe_remapped[k] for k in sorted(fp_keys)]

    return comparisons, false_negatives, false_positives


def find_rejected_false_negative_overlaps(
    false_negatives: list[AggregatedGene],
    rejected_genes: list[RejectedGeneInfo] | None,
) -> dict[str, tuple[AggregatedGene, list[RejectedGeneInfo]]]:
    """Find false-negative genes that were extracted by the LLM but rejected.

    Returns a dict keyed by FN gene symbol mapping to (AggregatedGene, matching
    rejected entries).  Also checks ``_REVERSE_GENE_ALIASES`` so that e.g.
    a rejected ``COL4A1`` is matched to the FN ``COL4A1/2``.
    """
    if not false_negatives or not rejected_genes:
        return {}

    # Build lookup: normalized FN symbol → AggregatedGene
    fn_by_symbol: dict[str, AggregatedGene] = {
        g.symbol.upper(): g for g in false_negatives
    }

    overlaps: dict[str, tuple[AggregatedGene, list[RejectedGeneInfo]]] = {}

    for rg in rejected_genes:
        norm = rg.symbol.upper()

        # Direct match
        if norm in fn_by_symbol:
            target = norm
        # Alias match (e.g. rejected COL4A1 → FN COL4A1/2)
        elif (
            norm in _REVERSE_GENE_ALIASES
            and _REVERSE_GENE_ALIASES[norm] in fn_by_symbol
        ):
            target = _REVERSE_GENE_ALIASES[norm]
        else:
            continue

        if target not in overlaps:
            overlaps[target] = (fn_by_symbol[target], [])
        overlaps[target][1].append(rg)

    return overlaps


# ---------------------------------------------------------------------------
# 6. Scoring
# ---------------------------------------------------------------------------

# Weight definitions
DETECTION_WEIGHT = 0.55
GWAS_WEIGHT = 0.15
MR_WEIGHT = 0.10
OMICS_WEIGHT = 0.10
PMID_WEIGHT = 0.10


def compute_gene_score(comp: GeneComparison) -> float:
    """Compute per-gene composite score."""
    detection_credit = 1.0  # Gene was detected (matched)

    field_scores: dict[str, float] = {}
    pmid_skipped = False
    for fc in comp.fields:
        field_scores[fc.field_name] = fc.score
        if fc.field_name == "PMIDs" and fc.status == MatchStatus.SKIPPED:
            pmid_skipped = True

    gwas = field_scores.get("GWAS Traits", 0.0)
    mr = field_scores.get("MR", 0.0)
    omics = field_scores.get("Omics", 0.0)
    pmid = field_scores.get("PMIDs", 0.0)

    if pmid_skipped:
        # Redistribute PMID weight proportionally to other field weights
        other_total = GWAS_WEIGHT + MR_WEIGHT + OMICS_WEIGHT
        if other_total > 0:
            scale = (GWAS_WEIGHT + MR_WEIGHT + OMICS_WEIGHT + PMID_WEIGHT) / other_total
        else:
            scale = 1.0
        return (
            DETECTION_WEIGHT * detection_credit
            + GWAS_WEIGHT * scale * gwas
            + MR_WEIGHT * scale * mr
            + OMICS_WEIGHT * scale * omics
        )
    else:
        return (
            DETECTION_WEIGHT * detection_credit
            + GWAS_WEIGHT * gwas
            + MR_WEIGHT * mr
            + OMICS_WEIGHT * omics
            + PMID_WEIGHT * pmid
        )


def compute_scores(
    comparisons: list[GeneComparison],
    false_negatives: list[AggregatedGene],
    false_positives: list[AggregatedGene],
    ref_count: int,
) -> ValidationScores:
    """Compute all aggregate validation scores."""
    scores = ValidationScores()

    tp = len(comparisons)
    fp = len(false_positives)
    fn = len(false_negatives)

    scores.true_positives = tp
    scores.false_positives = fp
    scores.false_negatives = fn
    scores.precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    scores.recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    if scores.precision + scores.recall > 0:
        p, r = scores.precision, scores.recall
        scores.f1 = 2 * p * r / (p + r)

    # Per-field aggregates over matched genes
    gwas_scores: list[float] = []
    mr_scores: list[float] = []
    omics_scores: list[float] = []
    pmid_scores: list[float] = []
    gene_scores: list[float] = []

    for comp in comparisons:
        gene_sc = compute_gene_score(comp)
        comp.gene_score = gene_sc
        gene_scores.append(gene_sc)

        for fc in comp.fields:
            if fc.field_name == "GWAS Traits":
                gwas_scores.append(fc.score)
            elif fc.field_name == "MR":
                mr_scores.append(fc.score)
            elif fc.field_name == "Omics":
                omics_scores.append(fc.score)
            elif fc.field_name == "PMIDs" and fc.status != MatchStatus.SKIPPED:
                pmid_scores.append(fc.score)

    def _mean(vals: list[float]) -> float:
        return sum(vals) / len(vals) if vals else 0.0

    scores.mean_gwas_jaccard = _mean(gwas_scores)
    scores.mr_accuracy = _mean(mr_scores)
    scores.mean_omics_jaccard = _mean(omics_scores)
    scores.mean_pmid_recall = _mean(pmid_scores)
    scores.mean_gene_score = _mean(gene_scores)

    # Overall composite
    scores.composite = (
        0.40 * scores.f1
        + 0.15 * scores.mean_gwas_jaccard
        + 0.10 * scores.mr_accuracy
        + 0.15 * scores.mean_omics_jaccard
        + 0.10 * scores.mean_pmid_recall
        + 0.10 * scores.mean_gene_score
    )

    return scores


# ---------------------------------------------------------------------------
# 7. Markdown generator
# ---------------------------------------------------------------------------


def _score_color(value: float) -> str:
    """Return background color for a score value."""
    if value >= 0.8:
        return "#c8e6c9"
    if value >= 0.5:
        return "#fff9c4"
    return "#ffcdd2"


def _pct(value: float) -> str:
    """Format a float as a percentage string."""
    return f"{value * 100:.1f}%"


def _letter_grade(score: float) -> str:
    """Return a letter grade for a 0.0–1.0 score."""
    if score >= 0.80:
        return "A"
    if score >= 0.65:
        return "B"
    if score >= 0.50:
        return "C"
    if score >= 0.35:
        return "D"
    return "F"


def _venn_diagram(tp: int, fn: int, fp: int, ref_count: int, pipe_count: int) -> str:
    """Return an inline SVG Venn diagram showing gene detection overlap."""
    # Layout constants
    w, h = 480, 260
    cx_left, cx_right, cy = 180, 300, 120
    r = 100

    # Colors
    ref_fill = "#667eea"  # accent blue
    pipe_fill = "#e87461"  # warm coral
    overlap_fill = "#5a4fcf"  # deeper blend

    return f"""\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" \
width="{w}" height="{h}" \
style="font-family:system-ui,sans-serif;max-width:100%;">
  <defs>
    <clipPath id="clip-left"><circle cx="{cx_left}" cy="{cy}" r="{r}"/></clipPath>
  </defs>
  <!-- Left circle (Reference) -->
  <circle cx="{cx_left}" cy="{cy}" r="{r}" \
fill="{ref_fill}" opacity="0.25" stroke="{ref_fill}" stroke-width="2"/>
  <!-- Right circle (Pipeline) -->
  <circle cx="{cx_right}" cy="{cy}" r="{r}" \
fill="{pipe_fill}" opacity="0.25" stroke="{pipe_fill}" stroke-width="2"/>
  <!-- Overlap highlight -->
  <circle cx="{cx_right}" cy="{cy}" r="{r}" \
clip-path="url(#clip-left)" fill="{overlap_fill}" opacity="0.25"/>
  <!-- FN label (left only) -->
  <text x="{cx_left - 45}" y="{cy - 8}" text-anchor="middle" \
font-size="22" font-weight="700" fill="#333">{fn}</text>
  <text x="{cx_left - 45}" y="{cy + 14}" text-anchor="middle" \
font-size="11" fill="#555">missed</text>
  <!-- TP label (overlap) -->
  <text x="{(cx_left + cx_right) // 2}" y="{cy - 8}" text-anchor="middle" \
font-size="22" font-weight="700" fill="#333">{tp}</text>
  <text x="{(cx_left + cx_right) // 2}" y="{cy + 14}" text-anchor="middle" \
font-size="11" fill="#555">matched</text>
  <!-- FP label (right only) -->
  <text x="{cx_right + 45}" y="{cy - 8}" text-anchor="middle" \
font-size="22" font-weight="700" fill="#333">{fp}</text>
  <text x="{cx_right + 45}" y="{cy + 14}" text-anchor="middle" \
font-size="11" fill="#555">extra</text>
  <!-- Circle labels -->
  <text x="{cx_left - 45}" y="{cy + r + 28}" text-anchor="middle" \
font-size="12" font-weight="600" fill="{ref_fill}">Reference ({ref_count})</text>
  <text x="{cx_right + 45}" y="{cy + r + 28}" text-anchor="middle" \
font-size="12" font-weight="600" fill="{pipe_fill}">Pipeline ({pipe_count})</text>
</svg>"""


def _generate_executive_summary(
    scores: ValidationScores,
    ref_count: int,
    pipe_count: int,
) -> str:
    """Return an auto-generated interpretive summary paragraph."""
    tp = scores.true_positives
    fp = scores.false_positives
    grade = _letter_grade(scores.composite)

    # Find strongest and weakest field metrics
    field_metrics: list[tuple[str, float]] = [
        ("GWAS Jaccard", scores.mean_gwas_jaccard),
        ("MR Accuracy", scores.mr_accuracy),
        ("Omics Jaccard", scores.mean_omics_jaccard),
        ("PMID Recall", scores.mean_pmid_recall),
    ]
    strongest = max(field_metrics, key=lambda x: x[1])
    weakest = min(field_metrics, key=lambda x: x[1])

    # Assessment sentence keyed to grade
    assessments: dict[str, str] = {
        "A": "The pipeline is performing well across detection and field extraction.",
        "B": "The pipeline shows solid performance with room for"
        " improvement in some areas.",
        "C": "The pipeline has moderate accuracy; several metrics need attention.",
        "D": "The pipeline is under-performing; significant improvements are needed.",
        "F": "The pipeline is failing to meet acceptable accuracy thresholds.",
    }
    assessment = assessments[grade]

    parts: list[str] = [
        f"The pipeline detected **{tp} of {ref_count}** reference genes"
        f" (recall {_pct(scores.recall)})",
    ]
    if fp:
        parts[0] += f" and reported **{fp}** false positive{'s' if fp != 1 else ''}."
    else:
        parts[0] += " with no false positives."

    parts.append(
        f"Among field-level metrics, **{strongest[0]}** was the strongest"
        f" at {_pct(strongest[1])}, while **{weakest[0]}** was the weakest"
        f" at {_pct(weakest[1])}."
    )
    parts.append(assessment)

    return " ".join(parts)


def _escape_html(text: str) -> str:
    """Minimal HTML escaping."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def generate_markdown(
    comparisons: list[GeneComparison],
    false_negatives: list[AggregatedGene],
    false_positives: list[AggregatedGene],
    scores: ValidationScores,
    ref_path: Path,
    pipe_path: Path,
    ref_count: int,
    pipe_count: int,
    *,
    fulltext_only: bool = False,
    local_pdfs: bool = False,
    rejected_genes: list[RejectedGeneInfo] | None = None,
) -> str:
    """Generate a full Markdown validation report."""
    lines: list[str] = []

    # --- Shared table styles ---
    lines.append("""\
<style>
.vr-table {
  border-collapse: collapse;
  font-size: 13px;
  font-family: system-ui, -apple-system, sans-serif;
  width: 100%;
  max-width: 900px;
  margin: 8px 0 16px;
}
.vr-table th {
  background: #f0f0f5;
  font-weight: 600;
  text-align: left;
  padding: 8px 10px;
  border-bottom: 2px solid #d0d0d8;
}
.vr-table td {
  padding: 6px 10px;
  border-bottom: 1px solid #e8e8ee;
  vertical-align: top;
}
.vr-table tr:hover {
  background: #f8f8fc;
}
.vr-table code {
  font-size: 12px;
  background: #f5f5fa;
  padding: 1px 4px;
  border-radius: 3px;
}
</style>
""")

    # --- Header with grade badge ---
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    grade = _letter_grade(scores.composite)
    lines.append("# Pipeline Validation Report\n")
    lines.append(f"## Grade: {grade} ({_pct(scores.composite)})\n")
    lines.append(f"**Generated:** {now}  ")
    lines.append(f"**Reference:** `{ref_path}` | **Pipeline report:** `{pipe_path}`  ")
    lines.append(
        f"**Reference genes:** {ref_count} | "
        f"**Pipeline genes (after alias merging):** {pipe_count}  "
    )
    if local_pdfs:
        filter_label = "Local PDFs only"
    elif fulltext_only:
        filter_label = "Full-text papers only"
    else:
        filter_label = "All papers (full-text + abstract)"
    lines.append(f"**Filter:** {filter_label}  ")
    lines.append(
        f"**Matched:** {scores.true_positives} | "
        f"**False Negatives:** {scores.false_negatives} | "
        f"**False Positives:** {scores.false_positives}\n"
    )

    # --- Executive Summary ---
    lines.append("## Executive Summary\n")
    lines.append(_generate_executive_summary(scores, ref_count, pipe_count) + "\n")

    # --- Gene Detection Venn Diagram ---
    lines.append("## Gene Detection Overview\n")
    lines.append(
        _venn_diagram(
            scores.true_positives,
            scores.false_negatives,
            scores.false_positives,
            ref_count,
            pipe_count,
        )
    )
    lines.append("")

    # --- Summary Scores ---
    lines.append("## Summary Scores\n")
    lines.append('<table class="vr-table">')
    lines.append("<tr><th>Metric</th><th>Value</th><th>Grade</th></tr>")

    score_rows = [
        ("Composite Score", scores.composite),
        ("Gene F1", scores.f1),
        ("Gene Precision", scores.precision),
        ("Gene Recall", scores.recall),
        ("Mean GWAS Jaccard", scores.mean_gwas_jaccard),
        ("MR Accuracy", scores.mr_accuracy),
        ("Mean Omics Jaccard", scores.mean_omics_jaccard),
        ("Mean PMID Recall", scores.mean_pmid_recall),
        ("Mean Gene Score", scores.mean_gene_score),
    ]

    for label, value in score_rows:
        color = _score_color(value)
        row_grade = _letter_grade(value)
        lines.append(
            f"<tr><td>{label}</td><td>{_pct(value)}</td>"
            f'<td style="background-color:{color};text-align:center;'
            f'font-weight:600;border-radius:3px;">{row_grade}</td></tr>'
        )
    lines.append("</table>\n")

    # --- Gene Comparison Table ---
    sorted_comps = sorted(comparisons, key=lambda c: c.gene_score)
    lines.append("## Gene Comparison (sorted by score, worst first)\n")
    lines.append('<table class="vr-table">')
    lines.append(
        "<tr><th>Gene</th><th>Score</th><th>Conf.</th>"
        "<th>GWAS Traits</th><th>MR</th><th>Omics</th><th>PMIDs</th></tr>"
    )

    for comp in sorted_comps:
        field_map = {fc.field_name: fc for fc in comp.fields}
        row = f"<tr><td><b>{_escape_html(comp.gene)}</b></td>"
        sc = comp.gene_score
        row += (
            f'<td style="background-color:{_score_color(sc)};'
            f'text-align:center;">{_pct(sc)}</td>'
        )
        row += f"<td>{comp.mean_confidence:.2f}</td>"

        for fname in ["GWAS Traits", "MR", "Omics", "PMIDs"]:
            fc = field_map.get(fname)
            if fc:
                color = STATUS_COLORS[fc.status]
                ref_display = _escape_html(fc.ref_value)
                pipe_display = _escape_html(fc.pipe_value)
                row += (
                    f'<td style="background-color:{color};">'
                    f"<b>Ref:</b> {ref_display}<br>"
                    f"<b>Pipe:</b> {pipe_display}</td>"
                )
            else:
                row += "<td>—</td>"
        row += "</tr>"
        lines.append(row)

    lines.append("</table>\n")

    # --- False Negatives ---
    if false_negatives:
        lines.append("## False Negatives (reference only)\n")
        lines.append("Genes in the reference table but not found by the pipeline.\n")
        lines.append('<table class="vr-table">')
        lines.append(
            "<tr><th>Gene</th><th>GWAS Traits</th>"
            "<th>MR</th><th>Omics</th><th>PMIDs</th></tr>"
        )
        for gene in sorted(false_negatives, key=lambda g: g.symbol):
            traits = (
                ", ".join(sorted(gene.gwas_traits)) if gene.gwas_traits else "(empty)"
            )
            omics = ", ".join(sorted(gene.omics)) if gene.omics else "(empty)"
            pmids_str = (
                "(reference needed)"
                if gene.pmids_reference_needed
                else (", ".join(sorted(gene.pmids)) if gene.pmids else "(empty)")
            )
            lines.append(
                f'<tr style="background-color:#fff0f0;">'
                f"<td><b>{_escape_html(gene.symbol)}</b></td>"
                f"<td>{_escape_html(traits)}</td>"
                f"<td>{'Yes' if gene.mr else 'No'}</td>"
                f"<td>{_escape_html(omics)}</td>"
                f"<td>{_escape_html(pmids_str)}</td></tr>"
            )
        lines.append("</table>\n")

    # --- False Positives ---
    if false_positives:
        lines.append("## False Positives (pipeline only)\n")
        lines.append("Genes found by the pipeline but not in the reference table.\n")
        lines.append('<table class="vr-table">')
        lines.append(
            "<tr><th>Gene</th><th>GWAS Traits</th><th>MR</th><th>Omics</th>"
            "<th>Mean Confidence</th></tr>"
        )
        for gene in sorted(false_positives, key=lambda g: g.symbol):
            traits = (
                ", ".join(sorted(gene.gwas_traits)) if gene.gwas_traits else "(empty)"
            )
            omics = ", ".join(sorted(gene.omics)) if gene.omics else "(empty)"
            mean_conf = (
                f"{sum(gene.confidences) / len(gene.confidences):.2f}"
                if gene.confidences
                else "N/A"
            )
            lines.append(
                f'<tr style="background-color:#fffbf0;">'
                f"<td><b>{_escape_html(gene.symbol)}</b></td>"
                f"<td>{_escape_html(traits)}</td>"
                f"<td>{'Yes' if gene.mr else 'No'}</td>"
                f"<td>{_escape_html(omics)}</td>"
                f"<td>{mean_conf}</td></tr>"
            )
        lines.append("</table>\n")

    # --- Rejected Genes ---
    if rejected_genes:
        lines.append("## Rejected Genes (failed validation)\n")
        lines.append(
            "Genes extracted by the LLM but rejected during NCBI validation.\n"
        )
        lines.append('<table class="vr-table">')
        lines.append(
            "<tr><th>Gene</th><th>Protein</th><th>PMID</th>"
            "<th>Confidence</th><th>Rejection Reasons</th></tr>"
        )
        for rg in sorted(rejected_genes, key=lambda g: g.symbol):
            conf_color = _score_color(rg.confidence)
            reasons_str = "; ".join(rg.reasons) if rg.reasons else "(unknown)"
            lines.append(
                f'<tr style="background-color:#fff0f0;">'
                f"<td><b>{_escape_html(rg.symbol)}</b></td>"
                f"<td>{_escape_html(rg.protein_name) or '—'}</td>"
                f"<td>{_escape_html(rg.pmid)}</td>"
                f'<td style="background-color:{conf_color};text-align:center;">'
                f"{rg.confidence:.2f}</td>"
                f"<td>{_escape_html(reasons_str)}</td></tr>"
            )
        lines.append("</table>\n")

    # --- Potentially Recoverable Genes ---
    recoverable = find_rejected_false_negative_overlaps(false_negatives, rejected_genes)
    if recoverable:
        fn_total = len(false_negatives)
        lines.append("## Potentially Recoverable Genes\n")
        lines.append(
            f"These **{len(recoverable)} of {fn_total}** false-negative genes"
            " were actually extracted by the LLM but rejected during NCBI"
            " validation. Relaxing or correcting validation rules could"
            " recover them.\n"
        )
        lines.append('<table class="vr-table">')
        lines.append(
            "<tr><th>Gene</th><th>Ref GWAS Traits</th><th>Ref MR</th>"
            "<th>Ref Omics</th><th>Ref PMIDs</th><th>Rejected From</th>"
            "<th>LLM Confidence</th><th>Rejection Reasons</th></tr>"
        )
        for symbol in sorted(recoverable):
            fn_gene, rg_entries = recoverable[symbol]

            traits = (
                ", ".join(sorted(fn_gene.gwas_traits))
                if fn_gene.gwas_traits
                else "(empty)"
            )
            omics = ", ".join(sorted(fn_gene.omics)) if fn_gene.omics else "(empty)"
            pmids_str = (
                "(reference needed)"
                if fn_gene.pmids_reference_needed
                else (", ".join(sorted(fn_gene.pmids)) if fn_gene.pmids else "(empty)")
            )

            # Aggregate rejected entries
            rejected_pmids = sorted({rg.pmid for rg in rg_entries})
            confs = [rg.confidence for rg in rg_entries]
            mean_conf = sum(confs) / len(confs) if confs else 0.0
            conf_color = _score_color(mean_conf)

            # Deduplicate reasons across entries
            seen_reasons: list[str] = []
            seen_set: set[str] = set()
            for rg in rg_entries:
                for reason in rg.reasons:
                    if reason not in seen_set:
                        seen_set.add(reason)
                        seen_reasons.append(reason)
            reasons_str = "; ".join(seen_reasons) if seen_reasons else "(unknown)"

            lines.append(
                f'<tr style="background-color:#fff3e0;">'
                f"<td><b>{_escape_html(symbol)}</b></td>"
                f"<td>{_escape_html(traits)}</td>"
                f"<td>{'Yes' if fn_gene.mr else 'No'}</td>"
                f"<td>{_escape_html(omics)}</td>"
                f"<td>{_escape_html(pmids_str)}</td>"
                f"<td>{_escape_html(', '.join(rejected_pmids))}</td>"
                f'<td style="background-color:{conf_color};text-align:center;">'
                f"{mean_conf:.2f}</td>"
                f"<td>{_escape_html(reasons_str)}</td></tr>"
            )
        lines.append("</table>\n")

    # --- Methodology Notes ---
    lines.append("## Methodology Notes\n")
    lines.append("### Normalization Rules\n")
    lines.append(
        "- **Gene symbols**: Case-insensitive; `COL4A1/2` in reference"
        " maps to pipeline's `COL4A1` + `COL4A2`;"
        " NCBI-renamed genes resolved to current official symbol"
        " (e.g., `C6ORF195` → `LINC01600`)"
    )
    lines.append(
        "- **GWAS traits**: Comma-separated to set; "
        "`(none found)` = empty; aliases: `CMB` = "
        "`cerebral-microbleeds`, `NODDI` = `ICVF`"
    )
    lines.append("- **MR**: `Yes`/`No` to boolean; aggregated across papers via OR")
    lines.append(
        "- **Omics**: Comma-separated to set; tissue stripped"
        " after `;` for base-type comparison"
        " (e.g., `TWAS;brain` becomes `twas`)"
    )
    lines.append(
        "- **PMIDs**: Comma-separated to set; "
        "`(reference needed)` = skip comparison; "
        "non-numeric PMIDs (local-pdf mode) = skip\n"
    )
    lines.append("### Scoring Formulas\n")
    lines.append("- **Gene F1** = 2 x Precision x Recall / (Precision + Recall)")
    lines.append(
        "- **GWAS/Omics Jaccard** = |intersection| / |union|; both-empty = 1.0"
    )
    lines.append("- **MR Accuracy** = binary match (1.0 or 0.0)")
    lines.append(
        "- **PMID Recall** = |intersection| / |reference|; skipped if reference needed"
    )
    lines.append(
        "- **Per-gene score** = 0.55 x detection"
        " + 0.15 x gwas + 0.10 x mr"
        " + 0.10 x omics + 0.10 x pmid"
    )
    lines.append(
        "- **Composite** = 0.40 x F1"
        " + 0.15 x gwas_jaccard + 0.10 x mr_accuracy"
        " + 0.15 x omics_jaccard + 0.10 x pmid_recall"
        " + 0.10 x mean_gene_score"
    )
    lines.append("")
    lines.append("### Scoring Explained\n")
    lines.append(
        "The system scores at two levels: **individual genes** and the"
        " **overall pipeline run**.\n"
    )
    lines.append(
        "**Key terms.** *Precision* is the fraction of pipeline-reported"
        " genes that actually appear in the reference (i.e. how many of"
        " its calls are correct). *Recall* is the fraction of reference"
        " genes that the pipeline found (i.e. how many true genes it"
        " detected). *F1* is the harmonic mean (the reciprocal — one"
        " divided by the number — of the average of the reciprocals) of"
        " precision and recall —"
        " a single number that is high only when both are high, so it"
        " penalises a pipeline that finds many genes but gets lots wrong"
        " (low precision) just as much as one that is very selective but"
        " misses many (low recall). *Jaccard similarity* measures overlap"
        " between two sets: it divides the number of items they share by"
        " the total number of distinct items across both sets. A Jaccard"
        " of 1.0 means perfect agreement; 0.0 means no overlap at all."
        " When both the pipeline and reference sets are empty the score"
        " is treated as 1.0 (both agree nothing is present).\n"
    )
    lines.append(
        "**Per-gene score.** For each gene the pipeline correctly detected,"
        " over half the credit (55%) comes from simply finding the gene."
        " The remaining 45% is split among getting the right GWAS traits"
        " (15%, measured by Jaccard similarity between the reported and"
        " reference trait sets), MR status (10%, a binary match — right"
        " or wrong), omics evidence types (10%, also Jaccard), and citing"
        " the right PMIDs (10%, measured by recall — what fraction of the"
        " reference PMIDs were found). When PMIDs cannot be compared — for"
        ' example when the reference contains "(reference needed)" or'
        " when running in local-PDF mode — that 10% is redistributed"
        " proportionally among the other three field scores.\n"
    )
    lines.append(
        "**Overall composite score.** The single biggest factor (40%) is the"
        " F1 score — a single number that rewards a pipeline only when it"
        " both finds most reference genes (high recall) and avoids"
        " reporting spurious ones (high precision). The remaining 60%"
        " reflects how accurately the pipeline extracts each field's"
        " details, averaged across matched genes: GWAS trait overlap"
        " (15%, Jaccard), omics evidence overlap (15%, Jaccard), MR"
        " accuracy (10%, binary match), PMID recall (10%), plus the mean"
        " per-gene score (10%).\n"
    )
    lines.append(
        "**Interpreting the result.** A perfect pipeline matching the"
        " reference table exactly would score 100%. Scores above 80% are"
        " highlighted green, 50–80% yellow, and below 50% red."
    )
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 8. CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Compare pipeline gene extraction against a gold-standard reference table."
        ),
    )
    parser.add_argument(
        "pipeline_report",
        type=Path,
        help="Path to pipeline report JSON",
    )
    parser.add_argument(
        "--reference",
        type=Path,
        default=DEFAULT_REFERENCE_PATH,
        help=f"Path to reference CSV (default: {DEFAULT_REFERENCE_PATH})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory (default: same dir as report)",
    )
    filter_group = parser.add_mutually_exclusive_group()
    filter_group.add_argument(
        "--fulltext-only",
        action="store_true",
        default=False,
        help="Only include genes extracted from full-text papers "
        "(exclude abstract-only)",
    )
    filter_group.add_argument(
        "--local-pdfs",
        action="store_true",
        default=False,
        help="Restrict reference to genes from papers in the local-PDFs run "
        "(PDFs must be named by PMID, e.g. 12345678.pdf)",
    )

    args = parser.parse_args(argv)

    pipe_path: Path = args.pipeline_report
    ref_path: Path = args.reference
    output_dir: Path = args.output_dir or (_PROJECT_ROOT / "logs" / "md")

    if not pipe_path.exists():
        print(f"Error: Pipeline report not found: {pipe_path}", file=sys.stderr)
        sys.exit(1)
    if not ref_path.exists():
        print(f"Error: Reference CSV not found: {ref_path}", file=sys.stderr)
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)

    # Parse
    ref_genes = parse_reference_csv(ref_path)
    pipe_genes, fulltext_pmids, rejected_genes = parse_pipeline_json(
        pipe_path, fulltext_only=args.fulltext_only
    )

    # In fulltext-only or local-pdfs mode, restrict the reference to genes
    # linked to at least one fulltext PMID so abstract-only genes don't inflate FN.
    if args.local_pdfs and len(fulltext_pmids) == 1:
        (single_pmid,) = fulltext_pmids
        ref_genes = filter_reference_for_single_pmid(ref_genes, single_pmid)
    elif args.fulltext_only or args.local_pdfs:
        ref_genes = filter_reference_for_fulltext(ref_genes, fulltext_pmids)

    # Compare
    comparisons, false_negatives, false_positives = compare_all(ref_genes, pipe_genes)

    # Count pipeline genes after remapping
    pipe_remapped = remap_pipeline_genes(pipe_genes)
    pipe_count = len(pipe_remapped)

    # Score
    scores = compute_scores(
        comparisons, false_negatives, false_positives, len(ref_genes)
    )

    # Generate report
    report = generate_markdown(
        comparisons,
        false_negatives,
        false_positives,
        scores,
        ref_path,
        pipe_path,
        len(ref_genes),
        pipe_count,
        fulltext_only=args.fulltext_only,
        local_pdfs=args.local_pdfs,
        rejected_genes=rejected_genes,
    )

    # Write report
    timestamp = datetime.now().strftime("%Y-%m-%d_%Hh%Mm%Ss")
    output_file = output_dir / f"validation_report_{timestamp}.md"
    output_file.write_text(report, encoding="utf-8")

    # Print summary to stdout
    print(f"Validation report written to: {output_file}")
    print(f"  Composite Score: {_pct(scores.composite)}")
    print(f"  Gene F1:         {_pct(scores.f1)}")
    print(f"  Precision:       {_pct(scores.precision)}")
    print(f"  Recall:          {_pct(scores.recall)}")
    print(f"  Matched: {scores.true_positives} / {len(ref_genes)} reference genes")
    print(f"  False Positives: {scores.false_positives}")
    print(f"  False Negatives: {scores.false_negatives}")


if __name__ == "__main__":
    main()
