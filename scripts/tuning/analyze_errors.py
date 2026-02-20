"""Categorize every gene into TP / FP / FN_threshold / FN_miss.

Outputs structured CSVs for downstream analysis (score distribution feeds
into calibrate_threshold.py).

Usage:
    python scripts/tuning/analyze_errors.py <pipeline_report.json> \
        [--reference <csv>] [--local-pdfs] [--output-dir logs/tuning/]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))
sys.path.insert(0, str(_PROJECT_ROOT))

from validate_pipeline import (  # noqa: E402
    compare_all,
    filter_reference_for_fulltext,
    filter_reference_for_single_pmid,
    find_rejected_false_negative_overlaps,
    normalize_gene_symbol,
    parse_pipeline_json,
    parse_reference_csv,
    remap_pipeline_genes,
)

DEFAULT_REFERENCE_PATH = (
    _PROJECT_ROOT / "data" / "test_data" / "gold_standard_v2.csv"
)


def analyze_errors(
    pipeline_report: Path,
    reference: Path,
    output_dir: Path,
    *,
    local_pdfs: bool = False,
) -> tuple[Path, Path]:
    """Run error analysis and write output CSVs.

    Returns (error_analysis_path, score_distribution_path).
    """
    with open(pipeline_report, encoding="utf-8") as f:
        report = json.load(f)

    # Parse
    pipe_genes, fulltext_pmids, rejected_genes = parse_pipeline_json(pipeline_report)
    ref_genes = parse_reference_csv(reference)

    # Filter reference
    if local_pdfs and len(fulltext_pmids) == 1:
        (single_pmid,) = fulltext_pmids
        ref_genes = filter_reference_for_single_pmid(ref_genes, single_pmid)
    elif local_pdfs:
        ref_genes = filter_reference_for_fulltext(ref_genes, fulltext_pmids)

    # Compare
    comparisons, false_negatives, false_positives = compare_all(ref_genes, pipe_genes)

    # Categorize FN into threshold vs miss
    overlaps = find_rejected_false_negative_overlaps(false_negatives, rejected_genes)
    fn_threshold_symbols = set(overlaps.keys())

    # Build rejected gene lookup
    rejected_by_symbol: dict[str, list[dict]] = {}
    for rg in rejected_genes:
        sym = normalize_gene_symbol(rg.symbol)
        if sym not in rejected_by_symbol:
            rejected_by_symbol[sym] = []
        rejected_by_symbol[sym].append(
            {
                "confidence": rg.confidence,
                "reasons": rg.reasons,
            }
        )

    # --- Output 1: Error analysis CSV ---
    timestamp = datetime.now().strftime("%Y-%m-%d_%Hh%Mm%Ss")
    error_dir = output_dir / "error_analyses"
    error_dir.mkdir(parents=True, exist_ok=True)
    error_path = error_dir / f"error_analysis_{timestamp}.csv"

    error_rows: list[dict[str, str]] = []

    # True positives
    pipe_remapped = remap_pipeline_genes(pipe_genes)
    for comp in comparisons:
        sym = comp.gene
        ref_g = ref_genes.get(sym)
        pipe_g = pipe_remapped.get(sym)
        mean_conf = comp.mean_confidence

        error_rows.append(
            {
                "gene_symbol": sym,
                "category": "TP",
                "confidence": f"{mean_conf:.2f}",
                "rejection_reason": "",
                "ref_gwas_traits": ", ".join(sorted(ref_g.gwas_traits))
                if ref_g
                else "",
                "ref_mr": "Yes" if (ref_g and ref_g.mr) else "No",
                "ref_omics": ", ".join(sorted(ref_g.omics)) if ref_g else "",
                "ref_pmids": ", ".join(sorted(ref_g.pmids)) if ref_g else "",
                "pipe_gwas_traits": ", ".join(sorted(pipe_g.gwas_traits))
                if pipe_g
                else "",
                "pipe_confidence": f"{mean_conf:.2f}",
            }
        )

    # False positives
    for gene in false_positives:
        mean_conf = (
            sum(gene.confidences) / len(gene.confidences) if gene.confidences else 0.0
        )
        error_rows.append(
            {
                "gene_symbol": gene.symbol,
                "category": "FP",
                "confidence": f"{mean_conf:.2f}",
                "rejection_reason": "",
                "ref_gwas_traits": "",
                "ref_mr": "",
                "ref_omics": "",
                "ref_pmids": "",
                "pipe_gwas_traits": ", ".join(sorted(gene.gwas_traits)),
                "pipe_confidence": f"{mean_conf:.2f}",
            }
        )

    # False negatives
    for gene in false_negatives:
        sym = gene.symbol
        if sym in fn_threshold_symbols:
            category = "FN_threshold"
            _, rg_entries = overlaps[sym]
            confs = [rg.confidence for rg in rg_entries]
            mean_conf = sum(confs) / len(confs) if confs else 0.0
            reasons_set: set[str] = set()
            for rg in rg_entries:
                for reason in rg.reasons:
                    reasons_set.add(reason)
            rejection_reason = "; ".join(sorted(reasons_set))
        else:
            category = "FN_miss"
            mean_conf = 0.0
            rejection_reason = "Not extracted by LLM"

        error_rows.append(
            {
                "gene_symbol": sym,
                "category": category,
                "confidence": f"{mean_conf:.2f}" if mean_conf > 0 else "",
                "rejection_reason": rejection_reason,
                "ref_gwas_traits": ", ".join(sorted(gene.gwas_traits)),
                "ref_mr": "Yes" if gene.mr else "No",
                "ref_omics": ", ".join(sorted(gene.omics)),
                "ref_pmids": ", ".join(sorted(gene.pmids)),
                "pipe_gwas_traits": "",
                "pipe_confidence": "",
            }
        )

    error_fields = [
        "gene_symbol",
        "category",
        "confidence",
        "rejection_reason",
        "ref_gwas_traits",
        "ref_mr",
        "ref_omics",
        "ref_pmids",
        "pipe_gwas_traits",
        "pipe_confidence",
    ]
    with open(error_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=error_fields)
        writer.writeheader()
        writer.writerows(error_rows)

    # --- Output 2: Score distribution CSV ---
    score_dir = output_dir / "score_distributions"
    score_dir.mkdir(parents=True, exist_ok=True)
    score_path = score_dir / f"score_distribution_{timestamp}.csv"

    # Build reference symbol set for is_in_reference check
    ref_symbols = set(ref_genes.keys())

    score_rows: list[dict[str, str]] = []

    # Collect all genes from papers_detail (accepted + rejected)
    for paper in report.get("papers_detail", []):
        paper_pmid = str(paper.get("pmid", ""))

        # Accepted genes
        for g in paper.get("genes", []):
            sym = normalize_gene_symbol(g.get("gene_symbol", ""))
            if not sym:
                continue
            score_rows.append(
                {
                    "gene_symbol": sym,
                    "confidence": str(g.get("confidence", "")),
                    "is_in_reference": str(sym in ref_symbols),
                    "was_accepted": "True",
                    "pmid": paper_pmid,
                }
            )

        # Rejected genes
        for rg in paper.get("rejected_genes", []):
            gene_data = rg.get("gene", {})
            sym = normalize_gene_symbol(gene_data.get("gene_symbol", ""))
            if not sym:
                continue
            score_rows.append(
                {
                    "gene_symbol": sym,
                    "confidence": str(gene_data.get("confidence", "")),
                    "is_in_reference": str(sym in ref_symbols),
                    "was_accepted": "False",
                    "pmid": paper_pmid,
                }
            )

    score_fields = [
        "gene_symbol",
        "confidence",
        "is_in_reference",
        "was_accepted",
        "pmid",
    ]
    with open(score_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=score_fields)
        writer.writeheader()
        writer.writerows(score_rows)

    # --- Print summary ---
    tp_count = sum(1 for r in error_rows if r["category"] == "TP")
    fp_count = sum(1 for r in error_rows if r["category"] == "FP")
    fn_thresh = sum(1 for r in error_rows if r["category"] == "FN_threshold")
    fn_miss = sum(1 for r in error_rows if r["category"] == "FN_miss")

    total = tp_count + fp_count + fn_thresh + fn_miss
    print(f"Error analysis written to: {error_path}")
    print(f"Score distribution written to: {score_path}")
    print("\n--- Summary ---")
    print(
        f"  TP:            {tp_count} ({tp_count / total * 100:.1f}%)" if total else ""
    )
    print(
        f"  FP:            {fp_count} ({fp_count / total * 100:.1f}%)" if total else ""
    )
    pct = fn_thresh / total * 100 if total else 0
    print(f"  FN (threshold): {fn_thresh} ({pct:.1f}%)" if total else "")
    print(f"  FN (miss):     {fn_miss} ({fn_miss / total * 100:.1f}%)" if total else "")
    print(f"  Total genes:   {total}")

    if fn_thresh > 0:
        print("\n--- Threshold False Negatives (recoverable) ---")
        for sym in sorted(fn_threshold_symbols):
            _, rg_entries = overlaps[sym]
            confs = [rg.confidence for rg in rg_entries]
            reasons = set()
            for rg in rg_entries:
                for r in rg.reasons:
                    reasons.add(r)
            print(f"  {sym}: confidence={confs}, reasons={sorted(reasons)}")

    return error_path, score_path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Categorize pipeline genes into TP/FP/FN_threshold/FN_miss."
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
        "--local-pdfs",
        action="store_true",
        default=False,
        help="Use local-PDF reference filtering",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_PROJECT_ROOT / "logs" / "tuning",
        help="Output directory for CSVs (default: logs/tuning/)",
    )

    args = parser.parse_args(argv)

    if not args.pipeline_report.exists():
        print(f"Error: Report not found: {args.pipeline_report}", file=sys.stderr)
        sys.exit(1)
    if not args.reference.exists():
        print(f"Error: Reference not found: {args.reference}", file=sys.stderr)
        sys.exit(1)

    analyze_errors(
        args.pipeline_report,
        args.reference,
        args.output_dir,
        local_pdfs=args.local_pdfs,
    )


if __name__ == "__main__":
    main()
