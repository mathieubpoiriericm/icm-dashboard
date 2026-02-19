"""Append tuning metrics from a pipeline run to the tracking CSV.

Reads a pipeline JSON report, runs comparison against reference using
validate_pipeline functions, and appends a row to logs/tuning/tuning_runs.csv.

Usage:
    python scripts/tuning/track_run.py --pipeline-report <json> [--reference <csv>] \
        [--local-pdfs] [--notes "..."]
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

# Add project root to path so we can import validate_pipeline
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))
sys.path.insert(0, str(_PROJECT_ROOT))

from validate_pipeline import (  # noqa: E402
    compare_all,
    compute_scores,
    filter_reference_for_fulltext,
    filter_reference_for_single_pmid,
    find_rejected_false_negative_overlaps,
    parse_pipeline_json,
    parse_reference_csv,
)

DEFAULT_REFERENCE_PATH = _PROJECT_ROOT / "data" / "test_data" / "gold_standard_2.csv"
TRACKING_CSV = _PROJECT_ROOT / "logs" / "tuning" / "tuning_runs.csv"

CSV_COLUMNS = [
    "run_id",
    "timestamp",
    "prompt_version",
    "confidence_threshold",
    "llm_model",
    "llm_effort",
    "total_extracted",
    "total_validated",
    "total_rejected",
    "acceptance_rate",
    "true_positives",
    "false_positives",
    "fn_threshold",
    "fn_miss",
    "precision",
    "recall",
    "f1",
    "f2",
    "composite_score",
    "estimated_cost_usd",
    "input_tokens",
    "output_tokens",
    "thinking_tokens",
    "total_processing_time",
    "llm_time",
    "notes",
]


def _compute_f_beta(precision: float, recall: float, beta: float) -> float:
    """Compute F-beta score."""
    if precision + recall == 0:
        return 0.0
    b2 = beta**2
    return (1 + b2) * precision * recall / (b2 * precision + recall)


def _next_run_id(csv_path: Path) -> int:
    """Get the next run_id from existing CSV, or 1 if none exists."""
    if not csv_path.exists():
        return 1
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        max_id = 0
        for row in reader:
            with contextlib.suppress(ValueError, KeyError):
                max_id = max(max_id, int(row["run_id"]))
    return max_id + 1


def _read_previous_row(csv_path: Path) -> dict[str, str] | None:
    """Read the last row from the tracking CSV."""
    if not csv_path.exists():
        return None
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    return rows[-1] if rows else None


def _print_diff(current: dict[str, str], previous: dict[str, str]) -> None:
    """Print metric differences between current and previous run."""
    print("\n--- Diff vs. previous run ---")
    diff_keys = [
        "precision",
        "recall",
        "f1",
        "f2",
        "composite_score",
        "true_positives",
        "false_positives",
        "fn_threshold",
        "fn_miss",
        "total_extracted",
        "total_validated",
        "total_rejected",
        "acceptance_rate",
        "estimated_cost_usd",
        "total_processing_time",
        "llm_time",
    ]
    for key in diff_keys:
        cur_val = current.get(key, "")
        prev_val = previous.get(key, "")
        if cur_val == prev_val:
            continue
        try:
            cur_f = float(cur_val)
            prev_f = float(prev_val)
            delta = cur_f - prev_f
            sign = "+" if delta > 0 else ""
            print(f"  {key}: {prev_val} -> {cur_val} ({sign}{delta:.4f})")
        except (ValueError, TypeError):
            print(f"  {key}: {prev_val} -> {cur_val}")


def track_run(
    pipeline_report: Path,
    reference: Path,
    *,
    local_pdfs: bool = False,
    notes: str = "",
) -> dict[str, str]:
    """Parse report, compute metrics, append row to CSV."""
    # Load pipeline report JSON
    with open(pipeline_report, encoding="utf-8") as f:
        report = json.load(f)

    # Extract pipeline config
    cfg = report.get("pipeline_config", {})
    genes_info = report.get("genes", {})
    token_info = report.get("token_usage", {})

    # Parse pipeline and reference
    pipe_genes, fulltext_pmids, rejected_genes = parse_pipeline_json(pipeline_report)
    ref_genes = parse_reference_csv(reference)

    # Filter reference appropriately
    if local_pdfs and len(fulltext_pmids) == 1:
        (single_pmid,) = fulltext_pmids
        ref_genes = filter_reference_for_single_pmid(ref_genes, single_pmid)
    elif local_pdfs:
        ref_genes = filter_reference_for_fulltext(ref_genes, fulltext_pmids)

    # Compare
    comparisons, false_negatives, false_positives = compare_all(ref_genes, pipe_genes)
    scores = compute_scores(
        comparisons, false_negatives, false_positives, len(ref_genes)
    )

    # Categorize false negatives: threshold vs miss
    overlaps = find_rejected_false_negative_overlaps(false_negatives, rejected_genes)
    fn_threshold = len(overlaps)
    fn_miss = len(false_negatives) - fn_threshold

    # Build row
    f2 = _compute_f_beta(scores.precision, scores.recall, 2.0)
    run_id = _next_run_id(TRACKING_CSV)

    # Extract per-paper timing (sum llm_time across all papers)
    papers_detail = report.get("papers_detail", [])
    total_llm_time = sum(p.get("llm_time", 0) for p in papers_detail)

    row = {
        "run_id": str(run_id),
        "timestamp": datetime.now(UTC).isoformat(),
        "prompt_version": cfg.get("prompt_version", "unknown"),
        "confidence_threshold": str(cfg.get("confidence_threshold", "")),
        "llm_model": cfg.get("model", ""),
        "llm_effort": cfg.get("effort", ""),
        "total_extracted": str(genes_info.get("extracted", 0)),
        "total_validated": str(genes_info.get("validated", 0)),
        "total_rejected": str(genes_info.get("rejected", 0)),
        "acceptance_rate": f"{genes_info.get('acceptance_rate', 0):.4f}",
        "true_positives": str(scores.true_positives),
        "false_positives": str(scores.false_positives),
        "fn_threshold": str(fn_threshold),
        "fn_miss": str(fn_miss),
        "precision": f"{scores.precision:.4f}",
        "recall": f"{scores.recall:.4f}",
        "f1": f"{scores.f1:.4f}",
        "f2": f"{f2:.4f}",
        "composite_score": f"{scores.composite:.4f}",
        "estimated_cost_usd": str(token_info.get("estimated_cost_usd", "")),
        "input_tokens": str(token_info.get("input_tokens", "")),
        "output_tokens": str(token_info.get("output_tokens", "")),
        "thinking_tokens": str(token_info.get("thinking_tokens", "")),
        "total_processing_time": f"{report.get('total_processing_time', 0):.1f}",
        "llm_time": f"{total_llm_time:.1f}",
        "notes": notes,
    }

    # Read previous row for diff
    previous = _read_previous_row(TRACKING_CSV)

    # Append to CSV
    write_header = not TRACKING_CSV.exists()
    TRACKING_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(TRACKING_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)

    # Print summary
    print(f"Run #{run_id} tracked to {TRACKING_CSV}")
    print(f"  Precision:  {scores.precision:.4f}")
    print(f"  Recall:     {scores.recall:.4f}")
    print(f"  F1:         {scores.f1:.4f}")
    print(f"  F2:         {f2:.4f}")
    print(f"  Composite:  {scores.composite:.4f}")
    print(f"  FN (threshold): {fn_threshold}  FN (miss): {fn_miss}")

    if previous:
        _print_diff(row, previous)

    return row


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Track a pipeline run's metrics in the tuning CSV."
    )
    parser.add_argument(
        "--pipeline-report",
        type=Path,
        required=True,
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
        help="Use local-PDF reference filtering (single PMID mode)",
    )
    parser.add_argument(
        "--notes",
        type=str,
        default="",
        help="Free-text notes for this run",
    )

    args = parser.parse_args(argv)

    if not args.pipeline_report.exists():
        print(f"Error: Report not found: {args.pipeline_report}", file=sys.stderr)
        sys.exit(1)
    if not args.reference.exists():
        print(f"Error: Reference not found: {args.reference}", file=sys.stderr)
        sys.exit(1)

    track_run(
        args.pipeline_report,
        args.reference,
        local_pdfs=args.local_pdfs,
        notes=args.notes,
    )


if __name__ == "__main__":
    main()
