"""Compare Claude baseline vs base Gemma vs fine-tuned Gemma on the gold set.

Input: one pre-produced pipeline report JSON per config. The user runs the
pipeline separately for each config (using --llm-provider / --ollama-model
from Task 11), then feeds the three report paths here.

Usage:
  python scripts/finetune/eval_finetuned.py \\
      --report claude-baseline:logs/json/claude_report.json \\
      --report gemma4-base:logs/json/gemma_base_report.json \\
      --report svd-gemma:v1:logs/json/gemma_ft_report.json

  python scripts/finetune/eval_finetuned.py --recommend --tag svd-gemma:v1
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

# Ensure project root is on sys.path when script is run directly.
_project_root = str(Path(__file__).resolve().parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from scripts.validate_pipeline import (  # noqa: E402
    DEFAULT_REFERENCE_PATH,
    compare_all,
    compute_scores,
    parse_pipeline_json,
    parse_reference_csv,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------


@dataclass
class EvalConfig:
    """Documentation of the 3 recommended evaluation configurations."""

    label: str
    llm_provider: str
    llm_model: str | None = None
    ollama_model: str | None = None
    prompt_version: str = "v5"


def build_configs(finetuned_tag: str) -> list[EvalConfig]:
    """Return the three recommended configs for comparison."""
    return [
        EvalConfig(
            label="claude-baseline",
            llm_provider="anthropic",
            llm_model="claude-opus-4-7",
            prompt_version="v5",
        ),
        EvalConfig(
            label="gemma4-base",
            llm_provider="ollama",
            ollama_model="gemma4:e4b",
            prompt_version="ollama_v1",
        ),
        EvalConfig(
            label=finetuned_tag,
            llm_provider="ollama",
            ollama_model=finetuned_tag,
            prompt_version="ollama_v1",
        ),
    ]


# ---------------------------------------------------------------------------
# Argument parsing helpers
# ---------------------------------------------------------------------------


def parse_report_arg(raw: str) -> tuple[str, Path]:
    """Parse a --report arg like 'label:path'. Splits on the LAST ':' so labels
    containing colons (e.g. 'svd-gemma:v1') work."""
    if ":" not in raw:
        raise ValueError(
            f"--report must be 'label:path', got {raw!r}. Example: "
            f"'claude-baseline:logs/json/report.json'."
        )
    label, _, path = raw.rpartition(":")
    return label, Path(path)


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------


def _print_recommendation(tag: str) -> None:
    configs = build_configs(tag)
    print("Recommended evaluation configurations:\n")
    for c in configs:
        model_desc = c.ollama_model or c.llm_model
        print(
            f"  {c.label:24} provider={c.llm_provider}"
            f" model={model_desc} prompt={c.prompt_version}"
        )
    print()
    print("Produce one pipeline report per configuration, then feed them in:")
    print()
    print("  # 1) Claude baseline")
    print("  python pipeline/main.py --pmids <gold-pmids-file> --skip-validation")
    print()
    print("  # 2) base Gemma 4")
    print("  python pipeline/main.py --pmids <gold-pmids-file> --skip-validation \\")
    print("      --llm-provider ollama --ollama-model gemma4:e4b")
    print()
    print(f"  # 3) fine-tuned ({tag})")
    print("  python pipeline/main.py --pmids <gold-pmids-file> --skip-validation \\")
    print(f"      --llm-provider ollama --ollama-model {tag}")
    print()
    print("Then:")
    print(
        f"  python scripts/finetune/eval_finetuned.py \\\n"
        f"      --report claude-baseline:<claude-report.json> \\\n"
        f"      --report gemma4-base:<gemma-base-report.json> \\\n"
        f"      --report {tag}:<gemma-ft-report.json>"
    )


def _format_row(label: str, scores) -> str:  # noqa: ANN001
    """Format one row of the comparison table. `scores` is ValidationScores."""
    p = scores.precision
    r = scores.recall
    f1 = scores.f1
    mean_gene = scores.mean_gene_score
    composite = scores.composite
    return (
        f"{label:24} {p:6.3f}   {r:6.3f}   {f1:6.3f}   "
        f"{mean_gene:8.3f}   {composite:9.3f}"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report",
        action="append",
        default=[],
        help="label:path-to-pipeline-report.json (repeatable)",
    )
    parser.add_argument(
        "--reference",
        type=Path,
        default=DEFAULT_REFERENCE_PATH,
        help="Gold-standard reference CSV (default: gold_standard_v2.csv).",
    )
    parser.add_argument(
        "--recommend",
        action="store_true",
        help="Print recommended configs and the commands to produce reports.",
    )
    parser.add_argument(
        "--tag",
        default="svd-gemma:v1",
        help="Fine-tuned Ollama tag (used by --recommend).",
    )
    args = parser.parse_args()

    if args.recommend:
        _print_recommendation(args.tag)
        return

    if not args.report:
        parser.error("at least one --report is required (or use --recommend)")

    ref_genes = parse_reference_csv(args.reference)
    ref_count = len(ref_genes)

    header = (
        f"{'Config':24} {'P':>6}   {'R':>6}   {'F1':>6}   "
        f"{'MeanGene':>8}   {'Composite':>9}"
    )
    print(header)
    print("-" * len(header))

    for raw in args.report:
        label, path = parse_report_arg(raw)
        try:
            pipe_genes, _fulltext_pmids, _rejected = parse_pipeline_json(path)
        except FileNotFoundError:
            logger.error("Report not found: %s", path)
            continue


        comparisons, false_negatives, false_positives = compare_all(
            ref_genes, pipe_genes
        )
        scores = compute_scores(
            comparisons, false_negatives, false_positives, ref_count
        )
        print(_format_row(label, scores))


if __name__ == "__main__":
    main()
