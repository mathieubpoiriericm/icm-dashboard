"""Comprehensive pipeline reporting: JSON export and rich CLI summary.

Assembles per-paper gene data, metrics, and configuration into a single
report structure used for both the JSON file and terminal output.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, TypedDict

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from pipeline.config import MODEL_PRICING, PipelineConfig
from pipeline.data_merger import MergeResult
from pipeline.llm_extraction import GeneEntry
from pipeline.quality_metrics import PipelineMetrics

# Prompt-caching multipliers applied to the base input price.
# The pipeline uses 1h TTL caches (see prompts.py), so writes cost 2x base.
_CACHE_WRITE_MULTIPLIER: float = 2.0  # 1h TTL
_CACHE_READ_MULTIPLIER: float = 0.1


class PaperSummary(TypedDict):
    """Serialisable summary for one processed paper."""

    pmid: str
    fulltext: bool
    source: str
    error: str | None
    gene_count: int
    genes: list[dict[str, Any]]
    rejected_gene_count: int
    rejected_genes: list[dict[str, Any]]
    processing_time: float


class PipelineRunData(TypedDict, total=False):
    """Full run data used by both JSON writer and rich printer."""

    timestamp: str
    total_processing_time: float
    total_compute_time: float
    pipeline_config: dict[str, Any]
    search: dict[str, int]
    papers: dict[str, Any]
    genes: dict[str, Any]
    token_usage: dict[str, Any]
    database: MergeResult | None
    batch_validation_warnings: list[str]
    papers_detail: list[PaperSummary]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
) -> float | None:
    """Estimate USD cost from token counts and model pricing.

    Accounts for prompt-caching: cache writes are charged at 2x the base
    input price (1h TTL) and cache reads at 0.1x.

    Returns None if the model is not in the pricing table.
    """
    pricing = MODEL_PRICING.get(model)
    if pricing is None:
        return None
    input_price, output_price = pricing
    return (
        input_tokens * input_price
        + cache_creation_input_tokens * input_price * _CACHE_WRITE_MULTIPLIER
        + cache_read_input_tokens * input_price * _CACHE_READ_MULTIPLIER
        + output_tokens * output_price
    ) / 1_000_000


def _round_cost(cost: float) -> float:
    """Round cost to 2 decimal places using round-half-up."""
    return float(Decimal(str(cost)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _paper_results_to_summaries(
    results: list[Any],
) -> list[PaperSummary]:
    """Convert PaperResult objects to serialisable dicts."""
    summaries: list[PaperSummary] = []
    for r in results:
        genes_data = [g.model_dump() for g in r.genes] if r.genes else []
        rejected_data = [
            {"gene": rg.gene.model_dump(), "reasons": rg.reasons}
            for rg in getattr(r, "rejected_genes", [])
        ]
        summaries.append(
            {
                "pmid": r.pmid,
                "fulltext": r.fulltext,
                "source": r.source,
                "error": r.error,
                "gene_count": len(r.genes),
                "genes": genes_data,
                "rejected_gene_count": len(rejected_data),
                "rejected_genes": rejected_data,
                "processing_time": getattr(r, "processing_time", 0.0),
            }
        )
    return summaries


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _build_common_run_data(
    metrics: PipelineMetrics,
    results: list[Any],
    batch_warnings: list[str],
    config: PipelineConfig,
    total_duration: float,
    pipeline_config: dict[str, Any],
) -> PipelineRunData:
    """Assemble the fields shared by all three run-data builders."""
    tu = metrics.token_usage
    cost = _estimate_cost(
        config.llm_model,
        tu.input_tokens,
        tu.output_tokens,
        tu.cache_creation_input_tokens,
        tu.cache_read_input_tokens,
    )

    failed_count = sum(1 for r in results if not r.succeeded)
    total_compute_time = sum(getattr(r, "processing_time", 0.0) for r in results)

    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "total_processing_time": total_duration,
        "total_compute_time": total_compute_time,
        "pipeline_config": pipeline_config,
        "papers": {
            "processed": metrics.papers_processed,
            "fulltext": metrics.fulltext_retrieved,
            "abstract_only": metrics.abstract_only,
            "fulltext_rate": round(metrics.fulltext_rate, 4),
            "failed": failed_count,
        },
        "genes": {
            "extracted": metrics.genes_extracted,
            "validated": metrics.genes_validated,
            "rejected": metrics.genes_rejected,
            "acceptance_rate": round(metrics.gene_acceptance_rate, 4),
        },
        "token_usage": {
            "input_tokens": tu.input_tokens,
            "output_tokens": tu.output_tokens,
            "cache_creation_input_tokens": tu.cache_creation_input_tokens,
            "cache_read_input_tokens": tu.cache_read_input_tokens,
            "total_tokens": tu.total_tokens,
            "cache_hit_rate": round(tu.cache_hit_rate, 4),
            "estimated_cost_usd": _round_cost(cost) if cost is not None else None,
        },
        "batch_validation_warnings": batch_warnings,
        "papers_detail": _paper_results_to_summaries(results),
    }


def build_run_data(
    metrics: PipelineMetrics,
    results: list[Any],
    all_genes: list[GeneEntry],
    gene_result: MergeResult | None,
    batch_warnings: list[str],
    config: PipelineConfig,
    days_back: int,
    dry_run: bool,
    total_pmids_found: int,
    new_pmids_count: int,
    total_duration: float = 0.0,
) -> PipelineRunData:
    """Assemble all pipeline run data into a single dict (standard mode)."""
    data = _build_common_run_data(
        metrics,
        results,
        batch_warnings,
        config,
        total_duration,
        pipeline_config={
            "model": config.llm_model,
            "days_back": days_back,
            "dry_run": dry_run,
            "confidence_threshold": config.confidence_threshold,
            "thinking_mode": "adaptive",
            "effort": config.llm_effort,
        },
    )
    data["search"] = {
        "pmids_found": total_pmids_found,
        "pmids_new": new_pmids_count,
        "pmids_skipped": total_pmids_found - new_pmids_count,
    }
    data["database"] = gene_result
    return data


def build_local_pdf_run_data(
    metrics: PipelineMetrics,
    results: list[Any],
    all_genes: list[GeneEntry],
    batch_warnings: list[str],
    config: PipelineConfig,
    pdf_dir: Path,
    skip_validation: bool,
    total_duration: float = 0.0,
) -> PipelineRunData:
    """Assemble run data for a local-PDF extraction run."""
    failed_count = sum(1 for r in results if not r.succeeded)
    data = _build_common_run_data(
        metrics,
        results,
        batch_warnings,
        config,
        total_duration,
        pipeline_config={
            "mode": "local_pdf",
            "model": config.llm_model,
            "pdf_directory": str(pdf_dir),
            "skip_validation": skip_validation,
            "confidence_threshold": config.confidence_threshold,
            "thinking_mode": "adaptive",
            "effort": config.llm_effort,
        },
    )
    data["papers"]["total"] = failed_count + metrics.papers_processed
    return data


def build_pmid_run_data(
    metrics: PipelineMetrics,
    results: list[Any],
    all_genes: list[GeneEntry],
    batch_warnings: list[str],
    config: PipelineConfig,
    pmid_file: Path,
    skip_validation: bool,
    total_duration: float = 0.0,
) -> PipelineRunData:
    """Assemble run data for a PMID-list extraction run."""
    failed_count = sum(1 for r in results if not r.succeeded)
    data = _build_common_run_data(
        metrics,
        results,
        batch_warnings,
        config,
        total_duration,
        pipeline_config={
            "mode": "pmid_list",
            "model": config.llm_model,
            "pmid_file": str(pmid_file),
            "skip_validation": skip_validation,
            "confidence_threshold": config.confidence_threshold,
            "thinking_mode": "adaptive",
            "effort": config.llm_effort,
        },
    )
    data["papers"]["total"] = failed_count + metrics.papers_processed
    return data


def write_comprehensive_report(data: PipelineRunData, log_dir: Path) -> Path:
    """Write the full run data as JSON.

    Args:
        data: PipelineRunData from build_run_data().
        log_dir: Directory for report files.

    Returns:
        Path to the written JSON file.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    path = log_dir / f"pipeline_report_{stamp}.json"
    path.write_text(json.dumps(data, indent=2, default=str) + "\n")
    return path


def print_rich_summary(data: PipelineRunData) -> None:
    """Print a rich-formatted pipeline summary directly to the console.

    Uses its own Console instance to bypass the logging RichHandler,
    avoiding double-formatting of rich markup.
    """
    console = Console()
    console.print()

    # --- Overview panel ---
    cfg = data.get("pipeline_config", {})
    papers = data.get("papers", {})
    run_mode = cfg.get("mode")
    is_offline_mode = run_mode in ("local_pdf", "pmid_list")

    if run_mode == "local_pdf":
        overview_lines = [
            f"[bold]Model:[/bold] {cfg.get('model', 'N/A')}",
            "[bold]Mode:[/bold] LOCAL PDF",
            f"[bold]Total time:[/bold] {data.get('total_processing_time', 0):.1f}s",
            f"[bold]Total compute:[/bold] {data.get('total_compute_time', 0):.1f}s",
            f"[bold]Directory:[/bold] {cfg.get('pdf_directory', 'N/A')}",
            f"[bold]Validation:[/bold] "
            f"{'skipped' if cfg.get('skip_validation') else 'enabled'}",
            f"[bold]PDFs processed:[/bold] {papers.get('processed', 0)} "
            f"/ {papers.get('total', 0)}",
        ]
    elif run_mode == "pmid_list":
        overview_lines = [
            f"[bold]Model:[/bold] {cfg.get('model', 'N/A')}",
            "[bold]Mode:[/bold] PMID LIST",
            f"[bold]Total time:[/bold] {data.get('total_processing_time', 0):.1f}s",
            f"[bold]Total compute:[/bold] {data.get('total_compute_time', 0):.1f}s",
            f"[bold]File:[/bold] {cfg.get('pmid_file', 'N/A')}",
            f"[bold]Validation:[/bold] "
            f"{'skipped' if cfg.get('skip_validation') else 'enabled'}",
            f"[bold]Papers processed:[/bold] {papers.get('processed', 0)} "
            f"/ {papers.get('total', 0)}"
            f" ({papers.get('fulltext', 0)} fulltext, "
            f"{papers.get('abstract_only', 0)} abstract)",
        ]
    else:
        search = data.get("search", {})
        mode = "DRY RUN" if cfg.get("dry_run") else "LIVE"
        overview_lines = [
            f"[bold]Model:[/bold] {cfg.get('model', 'N/A')}",
            f"[bold]Mode:[/bold] {mode}",
            f"[bold]Total time:[/bold] {data.get('total_processing_time', 0):.1f}s",
            f"[bold]Total compute:[/bold] {data.get('total_compute_time', 0):.1f}s",
            f"[bold]Days back:[/bold] {cfg.get('days_back', 'N/A')}",
            f"[bold]PMIDs found:[/bold] {search.get('pmids_found', 0)} "
            f"({search.get('pmids_new', 0)} new, "
            f"{search.get('pmids_skipped', 0)} skipped)",
            f"[bold]Papers processed:[/bold] {papers.get('processed', 0)} "
            f"({papers.get('fulltext', 0)} fulltext, "
            f"{papers.get('abstract_only', 0)} abstract)",
        ]

    if papers.get("failed", 0) > 0:
        overview_lines.append(f"[bold red]Papers failed:[/bold red] {papers['failed']}")

    console.print(
        Panel(
            "\n".join(overview_lines),
            title="[bold cyan]Pipeline Overview[/bold cyan]",
            border_style="cyan",
        )
    )

    # --- Papers table ---
    papers_detail = data.get("papers_detail", [])
    if papers_detail:
        papers_table = Table(
            title="Papers",
            show_lines=True,
            title_style="bold",
        )
        papers_table.add_column(
            "File" if run_mode == "local_pdf" else "PMID", style="bold"
        )
        papers_table.add_column("Source")
        papers_table.add_column("Genes", justify="right")
        papers_table.add_column("Time", justify="right")
        papers_table.add_column("Status")

        for p in papers_detail:
            error_msg = p.get("error")
            if error_msg:
                style = "red"
                status = Text(error_msg[:60], style="red")
            elif p["gene_count"] > 0:
                style = "green"
                status = Text("OK", style="green")
            else:
                style = "yellow"
                status = Text("0 genes", style="yellow")

            papers_table.add_row(
                p["pmid"],
                p.get("source", ""),
                str(p["gene_count"]),
                f"{p.get('processing_time', 0):.1f}s",
                status,
                style=style,
            )
        console.print(papers_table)

    # --- Genes table ---
    all_genes_flat: list[dict[str, Any]] = []
    for p in papers_detail:
        for g in p.get("genes", []):
            all_genes_flat.append(g)

    if all_genes_flat:
        genes_table = Table(
            title="Extracted Genes",
            show_lines=True,
            title_style="bold",
        )
        genes_table.add_column("Gene", style="bold")
        genes_table.add_column("Protein")
        genes_table.add_column("PMID")
        genes_table.add_column("Confidence", justify="right")
        genes_table.add_column("GWAS Traits")
        genes_table.add_column("MR", justify="center")
        genes_table.add_column("Omics")

        for g in all_genes_flat:
            conf = g.get("confidence", 0)
            if conf >= 0.9:
                conf_style = "green"
            elif conf >= 0.7:
                conf_style = "yellow"
            else:
                conf_style = "red"

            genes_table.add_row(
                g.get("gene_symbol", ""),
                g.get("protein_name") or "",
                g.get("pmid", ""),
                Text(f"{conf:.2f}", style=conf_style),
                ", ".join(g.get("gwas_trait", [])),
                Text("✓", style="green")
                if g.get("mendelian_randomization")
                else Text("✗", style="red"),
                ", ".join(g.get("omics_evidence", [])),
            )
        console.print(genes_table)

    # --- Rejected genes table ---
    all_rejected_flat: list[dict[str, Any]] = []
    for p in papers_detail:
        for rg in p.get("rejected_genes", []):
            all_rejected_flat.append(rg)

    if all_rejected_flat:
        rejected_table = Table(
            title="Rejected Genes",
            show_lines=True,
            title_style="bold red",
        )
        rejected_table.add_column("Gene", style="bold")
        rejected_table.add_column("Protein")
        rejected_table.add_column("PMID")
        rejected_table.add_column("Confidence", justify="right")
        rejected_table.add_column("Rejection Reasons")

        for rg in all_rejected_flat:
            g = rg.get("gene", {})
            conf = g.get("confidence", 0)
            rejected_table.add_row(
                g.get("gene_symbol", ""),
                g.get("protein_name") or "",
                g.get("pmid", ""),
                Text(f"{conf:.2f}", style="red"),
                Text("; ".join(rg.get("reasons", [])), style="dim"),
            )
        console.print(rejected_table)

    # --- Validation panel ---
    genes_info = data.get("genes", {})
    validation_lines = [
        f"[bold]Extracted genes:[/bold] {genes_info.get('extracted', 0)}",
        f"[bold]Validated genes:[/bold] {genes_info.get('validated', 0)}",
        f"[bold]Rejected genes:[/bold] {genes_info.get('rejected', 0)}",
        f"[bold]Acceptance rate:[/bold] {genes_info.get('acceptance_rate', 0):.1%}",
        f"[bold]Fulltext rate:[/bold] {papers.get('fulltext_rate', 0):.1%}",
    ]
    batch_warnings = data.get("batch_validation_warnings", [])
    if batch_warnings:
        validation_lines.append("")
        validation_lines.append(
            f"[bold yellow]Batch warnings ({len(batch_warnings)}):[/bold yellow]"
        )
        for w in batch_warnings:
            validation_lines.append(f"  [yellow]- {w}[/yellow]")

    console.print(
        Panel(
            "\n".join(validation_lines),
            title="[bold magenta]Validation[/bold magenta]",
            border_style="magenta",
        )
    )

    # --- Token & cost panel ---
    tu = data.get("token_usage", {})
    total = tu.get("total_tokens", 0)
    if total > 0:
        cost = tu.get("estimated_cost_usd")
        cost_str = f"${_round_cost(cost):.2f} USD" if cost is not None else "N/A"

        token_lines = [
            f"[bold]Input tokens:[/bold] {tu.get('input_tokens', 0):,}",
            f"[bold]Output tokens:[/bold] {tu.get('output_tokens', 0):,}",
            f"[bold]Cache read:[/bold] {tu.get('cache_read_input_tokens', 0):,}",
            f"[bold]Cache created:[/bold] {tu.get('cache_creation_input_tokens', 0):,}",
            f"[bold]Cache hit rate:[/bold] {tu.get('cache_hit_rate', 0):.1%}",
            f"[bold]Total tokens used:[/bold] {total:,}",
            f"[bold]Estimated cost:[/bold] {cost_str}",
        ]
        console.print(
            Panel(
                "\n".join(token_lines),
                title="[bold blue]Tokens & Cost[/bold blue]",
                border_style="blue",
            )
        )

    # --- Database panel (skip for offline modes) ---
    if not is_offline_mode:
        db = data.get("database")
        if db is not None:
            db_lines = [
                f"[bold]Inserted:[/bold] {db.get('inserted', 0)}",
                f"[bold]Updated:[/bold] {db.get('updated', 0)}",
            ]
        else:
            db_lines = ["[dim]Dry run — no database writes[/dim]"]

        console.print(
            Panel(
                "\n".join(db_lines),
                title="[bold green]Database[/bold green]",
                border_style="green",
            )
        )
    console.print()
