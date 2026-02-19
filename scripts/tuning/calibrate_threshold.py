"""Compute precision-recall curves and find optimal F-beta threshold.

Can consume either a pre-computed score_distribution CSV (from analyze_errors.py)
or compute the distribution directly from a pipeline report + reference.

Usage:
    python scripts/tuning/calibrate_threshold.py <score_distribution.csv> [--beta 2]
    python scripts/tuning/calibrate_threshold.py \
        --pipeline-report <json> --reference <csv> [--local-pdfs] [--beta 2]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from sklearn.metrics import precision_recall_curve

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))
sys.path.insert(0, str(_PROJECT_ROOT))

from validate_pipeline import (  # noqa: E402
    filter_reference_for_fulltext,
    filter_reference_for_single_pmid,
    normalize_gene_symbol,
    parse_pipeline_json,
    parse_reference_csv,
)

DEFAULT_REFERENCE_PATH = (
    _PROJECT_ROOT / "data" / "test_data" / "gold_standard_2.csv"
)
CURRENT_THRESHOLD = 0.65


def _load_score_distribution(path: Path) -> list[dict[str, str]]:
    """Load score distribution CSV."""
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _build_score_distribution_from_report(
    pipeline_report: Path,
    reference: Path,
    *,
    local_pdfs: bool = False,
) -> list[dict[str, str]]:
    """Build score distribution directly from pipeline report + reference."""
    with open(pipeline_report, encoding="utf-8") as f:
        report = json.load(f)

    _, fulltext_pmids, _ = parse_pipeline_json(pipeline_report)
    ref_genes = parse_reference_csv(reference)

    if local_pdfs and len(fulltext_pmids) == 1:
        (single_pmid,) = fulltext_pmids
        ref_genes = filter_reference_for_single_pmid(ref_genes, single_pmid)
    elif local_pdfs:
        ref_genes = filter_reference_for_fulltext(ref_genes, fulltext_pmids)

    ref_symbols = set(ref_genes.keys())
    rows: list[dict[str, str]] = []

    for paper in report.get("papers_detail", []):
        paper_pmid = str(paper.get("pmid", ""))
        for g in paper.get("genes", []):
            sym = normalize_gene_symbol(g.get("gene_symbol", ""))
            if sym:
                rows.append(
                    {
                        "gene_symbol": sym,
                        "confidence": str(g.get("confidence", "")),
                        "is_in_reference": str(sym in ref_symbols),
                        "was_accepted": "True",
                        "pmid": paper_pmid,
                    }
                )
        for rg in paper.get("rejected_genes", []):
            gene_data = rg.get("gene", {})
            sym = normalize_gene_symbol(gene_data.get("gene_symbol", ""))
            if sym:
                rows.append(
                    {
                        "gene_symbol": sym,
                        "confidence": str(gene_data.get("confidence", "")),
                        "is_in_reference": str(sym in ref_symbols),
                        "was_accepted": "False",
                        "pmid": paper_pmid,
                    }
                )

    return rows


def _compute_f_beta(precision: float, recall: float, beta: float) -> float:
    """Compute F-beta score."""
    if precision + recall == 0:
        return 0.0
    b2 = beta**2
    return (1 + b2) * precision * recall / (b2 * precision + recall)


def calibrate_threshold(
    score_rows: list[dict[str, str]],
    output_dir: Path,
    beta: float = 2.0,
    png_dir: Path | None = None,
) -> Path:
    """Run threshold calibration analysis.

    Returns path to the generated plot.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    if png_dir is None:
        png_dir = _PROJECT_ROOT / "logs" / "png" / "pr_curves"
    timestamp = datetime.now().strftime("%Y-%m-%d_%Hh%Mm%Ss")

    # Parse into arrays
    confidences: list[float] = []
    labels: list[int] = []  # 1 = in reference (should be accepted), 0 = not
    accepted: list[bool] = []

    for row in score_rows:
        try:
            conf = float(row["confidence"])
        except (ValueError, KeyError):
            continue
        is_ref = row.get("is_in_reference", "").strip().lower() == "true"
        was_acc = row.get("was_accepted", "").strip().lower() == "true"

        confidences.append(conf)
        labels.append(1 if is_ref else 0)
        accepted.append(was_acc)

    if not confidences:
        print("Error: No valid score data found.", file=sys.stderr)
        sys.exit(1)

    y_true = np.array(labels)
    y_scores = np.array(confidences)

    # Precision-recall curve (sklearn convention: higher score = more positive)
    precisions, recalls, thresholds = precision_recall_curve(y_true, y_scores)

    # Compute F1 and F-beta at each threshold
    f1_scores = []
    fb_scores = []
    for p, r in zip(precisions[:-1], recalls[:-1], strict=True):
        f1_scores.append(_compute_f_beta(p, r, 1.0))
        fb_scores.append(_compute_f_beta(p, r, beta))

    f1_arr = np.array(f1_scores)
    fb_arr = np.array(fb_scores)

    best_f1_idx = int(np.argmax(f1_arr))
    best_fb_idx = int(np.argmax(fb_arr))

    best_f1_threshold = float(thresholds[best_f1_idx])
    best_fb_threshold = float(thresholds[best_fb_idx])

    # --- Calibration check: clustering ---
    bin_edges = np.arange(0.0, 1.05, 0.05)
    hist_counts, _ = np.histogram(y_scores, bins=bin_edges)
    max_bin_frac = float(hist_counts.max()) / len(y_scores) if len(y_scores) > 0 else 0
    poor_calibration = max_bin_frac > 0.50
    max_bin_idx = int(np.argmax(hist_counts))
    max_bin_center = float((bin_edges[max_bin_idx] + bin_edges[max_bin_idx + 1]) / 2)

    # --- Threshold analysis CSV ---
    analysis_dir = output_dir / "threshold_analyses"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    analysis_path = analysis_dir / f"threshold_analysis_{timestamp}.csv"
    analysis_rows: list[dict[str, str]] = []

    test_thresholds = sorted(
        set(
            list(np.arange(0.0, 1.01, 0.05))
            + [CURRENT_THRESHOLD, best_f1_threshold, best_fb_threshold]
        )
    )

    for t in test_thresholds:
        predicted_positive = y_scores >= t
        tp_count = int(np.sum(predicted_positive & (y_true == 1)))
        fp_count = int(np.sum(predicted_positive & (y_true == 0)))
        fn_count = int(np.sum(~predicted_positive & (y_true == 1)))

        p = tp_count / (tp_count + fp_count) if (tp_count + fp_count) > 0 else 0.0
        r = tp_count / (tp_count + fn_count) if (tp_count + fn_count) > 0 else 0.0
        f1 = _compute_f_beta(p, r, 1.0)
        fb = _compute_f_beta(p, r, beta)

        analysis_rows.append(
            {
                "threshold": f"{t:.2f}",
                "precision": f"{p:.4f}",
                "recall": f"{r:.4f}",
                "f1": f"{f1:.4f}",
                f"f{beta:.0f}": f"{fb:.4f}",
                "gene_count": str(int(np.sum(predicted_positive))),
                "tp": str(tp_count),
                "fp": str(fp_count),
                "fn": str(fn_count),
            }
        )

    analysis_fields = [
        "threshold",
        "precision",
        "recall",
        "f1",
        f"f{beta:.0f}",
        "gene_count",
        "tp",
        "fp",
        "fn",
    ]
    with open(analysis_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=analysis_fields)
        writer.writeheader()
        writer.writerows(analysis_rows)

    # --- Plot ---
    png_dir.mkdir(parents=True, exist_ok=True)
    plot_path = png_dir / f"pr_curve_{timestamp}.png"

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

    # Subplot 1: Precision-Recall curve
    ax1.plot(recalls[:-1], precisions[:-1], "b-", linewidth=2, label="P-R curve")

    # Mark current threshold
    current_pred = y_scores >= CURRENT_THRESHOLD
    current_tp = int(np.sum(current_pred & (y_true == 1)))
    current_fp = int(np.sum(current_pred & (y_true == 0)))
    current_fn = int(np.sum(~current_pred & (y_true == 1)))
    denom_p = current_tp + current_fp
    current_p = current_tp / denom_p if denom_p > 0 else 0
    denom_r = current_tp + current_fn
    current_r = current_tp / denom_r if denom_r > 0 else 0
    ax1.plot(
        current_r,
        current_p,
        "rs",
        markersize=12,
        label=f"Current t={CURRENT_THRESHOLD}",
    )

    # Mark optimal F1
    f1_label = f"Best F1 t={best_f1_threshold:.2f} (F1={f1_arr[best_f1_idx]:.3f})"
    ax1.plot(
        recalls[best_f1_idx],
        precisions[best_f1_idx],
        "g^",
        markersize=12,
        label=f1_label,
    )

    # Mark optimal F-beta
    ax1.plot(
        recalls[best_fb_idx],
        precisions[best_fb_idx],
        "mD",
        markersize=12,
        label=(
            f"Best F{beta:.0f} t={best_fb_threshold:.2f}"
            f" (F{beta:.0f}={fb_arr[best_fb_idx]:.3f})"
        ),
    )

    ax1.set_xlabel("Recall", fontsize=12)
    ax1.set_ylabel("Precision", fontsize=12)
    ax1.set_title("Precision-Recall Curve", fontsize=14)
    ax1.legend(loc="lower left", fontsize=9)
    ax1.set_xlim(-0.05, 1.05)
    ax1.set_ylim(-0.05, 1.05)
    ax1.grid(True, alpha=0.3)

    # Subplot 2: Confidence histogram colored by TP/FP
    tp_mask = y_true == 1
    fp_mask = y_true == 0

    bins = np.arange(0.0, 1.05, 0.05)
    ax2.hist(
        [y_scores[tp_mask], y_scores[fp_mask]],
        bins=bins,
        label=["In reference (TP)", "Not in reference (FP)"],
        color=["#4CAF50", "#F44336"],
        alpha=0.7,
        stacked=True,
    )

    # Mark thresholds
    ax2.axvline(
        CURRENT_THRESHOLD,
        color="red",
        linestyle="--",
        linewidth=2,
        label=f"Current t={CURRENT_THRESHOLD}",
    )
    ax2.axvline(
        best_fb_threshold,
        color="purple",
        linestyle="--",
        linewidth=2,
        label=f"Best F{beta:.0f} t={best_fb_threshold:.2f}",
    )

    ax2.set_xlabel("Confidence Score", fontsize=12)
    ax2.set_ylabel("Gene Count", fontsize=12)
    ax2.set_title("Confidence Distribution by Reference Status", fontsize=14)
    ax2.legend(loc="upper left", fontsize=9)
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(plot_path, dpi=150, bbox_inches="tight")
    plt.close()

    # --- Print recommendation ---
    print(f"Threshold analysis written to: {analysis_path}")
    print(f"PR curve plot written to: {plot_path}")
    print("\n--- Calibration Results ---")
    print(f"  Total genes scored: {len(y_scores)}")
    in_ref = int(y_true.sum())
    not_ref = int((1 - y_true).sum())
    print(f"  In reference: {in_ref} / Not in reference: {not_ref}")

    if poor_calibration:
        print("\n  WARNING: Poor calibration detected!")
        pct = max_bin_frac * 100
        print(f"  {pct:.0f}% clustered around {max_bin_center:.2f}")

    cur_f1 = _compute_f_beta(current_p, current_r, 1.0)
    cur_fb = _compute_f_beta(current_p, current_r, beta)
    print(f"\n  Current threshold:  {CURRENT_THRESHOLD}")
    print(
        f"    P={current_p:.3f}  R={current_r:.3f}"
        f"  F1={cur_f1:.3f}  F{beta:.0f}={cur_fb:.3f}"
    )

    p_f1 = precisions[best_f1_idx]
    r_f1 = recalls[best_f1_idx]
    print(f"\n  Optimal F1 threshold: {best_f1_threshold:.2f}")
    print(f"    P={p_f1:.3f}  R={r_f1:.3f}  F1={f1_arr[best_f1_idx]:.3f}")

    p_fb = precisions[best_fb_idx]
    r_fb = recalls[best_fb_idx]
    print(f"\n  Optimal F{beta:.0f} threshold: {best_fb_threshold:.2f}")
    print(f"    P={p_fb:.3f}  R={r_fb:.3f}  F{beta:.0f}={fb_arr[best_fb_idx]:.3f}")

    # Compute deltas
    current_fb = _compute_f_beta(current_p, current_r, beta)

    if best_fb_threshold != CURRENT_THRESHOLD:
        best_pred = y_scores >= best_fb_threshold
        best_tp = int(np.sum(best_pred & (y_true == 1)))
        best_fp = int(np.sum(best_pred & (y_true == 0)))
        best_fn = int(np.sum(~best_pred & (y_true == 1)))
        d_p = best_tp + best_fp
        best_p = best_tp / d_p if d_p > 0 else 0
        d_r = best_tp + best_fn
        best_r = best_tp / d_r if d_r > 0 else 0
        best_fb_val = _compute_f_beta(best_p, best_r, beta)

        print(f"\n  Recommendation: lower threshold to {best_fb_threshold:.2f}")
        r_delta = best_r - current_r
        p_delta = best_p - current_p
        fb_delta = best_fb_val - current_fb
        print(f"    Recall: {current_r:.3f} -> {best_r:.3f} ({r_delta:+.3f})")
        print(f"    Precision: {current_p:.3f} -> {best_p:.3f} ({p_delta:+.3f})")
        print(
            f"    F{beta:.0f}: {current_fb:.3f} -> {best_fb_val:.3f} ({fb_delta:+.3f})"
        )
        n_cur = int(current_pred.sum())
        n_best = int(best_pred.sum())
        print(f"    Genes accepted: {n_cur} -> {n_best}")

    return plot_path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Compute precision-recall curves and optimal F-beta threshold."
    )
    parser.add_argument(
        "score_distribution",
        type=Path,
        nargs="?",
        default=None,
        help="Path to score_distribution CSV (from analyze_errors.py)",
    )
    parser.add_argument(
        "--pipeline-report",
        type=Path,
        default=None,
        help="Path to pipeline report JSON (alternative to score_distribution CSV)",
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
        "--beta",
        type=float,
        default=2.0,
        help="Beta value for F-beta score (default: 2, favoring recall)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_PROJECT_ROOT / "logs" / "tuning",
        help="Output directory (default: logs/tuning/)",
    )
    parser.add_argument(
        "--png-dir",
        type=Path,
        default=None,
        help="Output directory for PNG plots (default: logs/png/pr_curves/)",
    )

    args = parser.parse_args(argv)

    if args.score_distribution is not None:
        if not args.score_distribution.exists():
            print(f"Error: CSV not found: {args.score_distribution}", file=sys.stderr)
            sys.exit(1)
        score_rows = _load_score_distribution(args.score_distribution)
    elif args.pipeline_report is not None:
        if not args.pipeline_report.exists():
            print(f"Error: Report not found: {args.pipeline_report}", file=sys.stderr)
            sys.exit(1)
        score_rows = _build_score_distribution_from_report(
            args.pipeline_report,
            args.reference,
            local_pdfs=args.local_pdfs,
        )
    else:
        print(
            "Error: Provide either a score_distribution CSV or --pipeline-report",
            file=sys.stderr,
        )
        sys.exit(1)

    calibrate_threshold(
        score_rows, args.output_dir, beta=args.beta, png_dir=args.png_dir
    )


if __name__ == "__main__":
    main()
