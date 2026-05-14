"""Build MLX-LM chat-format JSONL from past Claude extractions.

Reads pipeline reports (logs/json/pipeline_report_*.json) for per-paper
extractions and pairs them with PDF text from a configurable directory.

Usage:
  python scripts/finetune/build_dataset.py \\
      --reports-glob "logs/json/pipeline_report_*.json" \\
      --pdf-dir data/test_data/pdf/ \\
      --min-confidence 0.7 \\
      --max-papers 800 \\
      --valid-frac 0.1 \\
      --max-paper-chars 20000 \\
      --out-dir data/finetune/svd_v1/ \\
      --gold-pmids-file <optional>
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_project_root = str(Path(__file__).resolve().parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from pipeline.pdf_retrieval import parse_local_pdf  # noqa: E402
from pipeline.prompts import build_extraction_prompt  # noqa: E402

logger = logging.getLogger(__name__)


@dataclass
class Paper:
    pmid: str
    fulltext: str
    genes: list[dict[str, Any]]


def load_reports(reports_glob: str) -> list[dict]:
    """Parse all JSON files matching the glob."""
    paths = sorted(glob.glob(reports_glob))
    reports: list[dict] = []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            reports.append(json.load(f))
    logger.info("Loaded %d pipeline reports from %s", len(reports), reports_glob)
    return reports


def extract_paper_records(
    reports: list[dict],
    *,
    min_confidence: float,
    gold_pmids: set[str],
) -> list[tuple[str, list[dict[str, Any]]]]:
    """Collapse reports into (pmid, kept_genes). Latest-report-wins on collision.

    Filters: gold-PMID exclusion, per-gene confidence >= min_confidence, drop
    papers with zero surviving genes. Caller is responsible for passing
    reports in chronological order (load_reports returns them alphabetically,
    which coincides with the timestamped filename format).
    """
    per_pmid: dict[str, list[dict[str, Any]]] = {}

    for report in reports:
        for paper in report.get("papers_detail", []):
            pmid = paper.get("pmid")
            if not pmid or pmid in gold_pmids:
                continue
            raw_genes = paper.get("genes") or []
            kept = [
                dict(g)
                for g in raw_genes
                if float(g.get("confidence", 0.0)) >= min_confidence
            ]
            if kept:
                per_pmid[pmid] = kept  # newer overwrites older

    return list(per_pmid.items())


def attach_pdf_text(
    records: list[tuple[str, list[dict[str, Any]]]],
    pdf_dir: Path,
    max_paper_chars: int,
) -> list[Paper]:
    """Pair each (pmid, genes) record with parsed PDF text from pdf_dir.

    Papers with missing or unparsable PDFs are dropped with a warning.
    Text truncated to max_paper_chars.
    """
    out: list[Paper] = []
    for pmid, genes in records:
        pdf_path = pdf_dir / f"{pmid}.pdf"
        try:
            text = parse_local_pdf(pdf_path)
        except FileNotFoundError:
            logger.warning("PMID %s: no PDF at %s", pmid, pdf_path)
            continue
        if not text:
            logger.warning("PMID %s: PDF parse failed for %s", pmid, pdf_path)
            continue
        if len(text) > max_paper_chars:
            text = text[:max_paper_chars]
        out.append(Paper(pmid=pmid, fulltext=text, genes=genes))
    return out


def paper_to_chat_record(p: Paper, prompt_version: str = "gemma_v1") -> dict:
    """Format a Paper as an MLX-LM chat JSONL record.

    The system + user messages are assembled exactly like the vLLM serving
    path (system_prompt + extraction_instructions joined) so fine-tuning
    inputs match inference inputs. `pmid` is stripped from each gene dict
    because the serving path assigns pmid post-extraction.
    """
    prompt = build_extraction_prompt(
        paper_text=p.fulltext,
        pmid=p.pmid,
        max_chars=len(p.fulltext) + 1,  # already truncated upstream
        prompt_version=prompt_version,
    )
    system_text = prompt.combined_system_text

    genes_clean = [{k: v for k, v in g.items() if k != "pmid"} for g in p.genes]
    assistant_obj = {"genes": genes_clean}
    assistant_text = json.dumps(assistant_obj, separators=(",", ":"))

    return {
        "messages": [
            {"role": "system", "content": system_text},
            {"role": "user", "content": prompt.user_text},
            {"role": "assistant", "content": assistant_text},
        ]
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports-glob", default="logs/json/pipeline_report_*.json")
    parser.add_argument("--pdf-dir", type=Path, default=Path("data/test_data/pdf"))
    parser.add_argument("--min-confidence", type=float, default=0.7)
    parser.add_argument("--max-papers", type=int, default=800)
    parser.add_argument("--valid-frac", type=float, default=0.1)
    parser.add_argument("--max-paper-chars", type=int, default=20_000)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument(
        "--gold-pmids-file",
        type=Path,
        default=None,
        help="File with one gold-standard PMID per line (excluded from training).",
    )
    parser.add_argument(
        "--prompt-version",
        default="gemma_v1",
        help="Prompt version to embed in chat records (default: gemma_v1).",
    )
    return parser


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    args = _build_parser().parse_args()

    gold_pmids: set[str] = set()
    if args.gold_pmids_file:
        gold_pmids = {
            line.strip()
            for line in args.gold_pmids_file.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        }
        logger.info("Excluding %d gold PMIDs", len(gold_pmids))

    reports = load_reports(args.reports_glob)
    records = extract_paper_records(
        reports,
        min_confidence=args.min_confidence,
        gold_pmids=gold_pmids,
    )
    logger.info("Found %d papers with qualifying extractions", len(records))

    papers = attach_pdf_text(records, args.pdf_dir, args.max_paper_chars)
    logger.info("Paired %d papers with PDF text", len(papers))

    # Cap, then time-ordered split: iteration order of per_pmid dict is already
    # insertion order (older reports first); newest papers end up last.
    papers = papers[: args.max_papers]
    n_valid = max(1, int(len(papers) * args.valid_frac))
    valid, train = papers[-n_valid:], papers[:-n_valid]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name, subset in [("train", train), ("valid", valid)]:
        path = args.out_dir / f"{name}.jsonl"
        with path.open("w", encoding="utf-8") as f:
            for p in subset:
                record = paper_to_chat_record(p, args.prompt_version)
                f.write(json.dumps(record) + "\n")
        logger.info("Wrote %d records to %s", len(subset), path)


if __name__ == "__main__":
    main()
