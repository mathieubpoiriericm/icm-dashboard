"""Comprehensive pipeline reporting: JSON export and rich CLI summary.

Assembles per-paper gene data, metrics, and configuration into a single
report structure used for both the JSON file and terminal output.
"""

import json
import logging
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, NotRequired, TypedDict

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from pipeline.config import PipelineConfig
from pipeline.data_merger import MergeResult
from pipeline.llm_providers import LLMProvider, get_provider
from pipeline.quality_metrics import PipelineMetrics

logger = logging.getLogger(__name__)


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
    pdf_parse_time: NotRequired[float]
    llm_time: NotRequired[float]
    validation_time: NotRequired[float]


class PipelineRunData(TypedDict):
    """Full run data used by both JSON writer and rich printer."""

    timestamp: str
    total_processing_time: float
    total_compute_time: float
    pipeline_config: dict[str, Any]
    search: NotRequired[dict[str, int]]
    papers: dict[str, Any]
    genes: dict[str, Any]
    token_usage: dict[str, Any]
    database: NotRequired[MergeResult | None]
    batch_validation_warnings: list[str]
    papers_detail: list[PaperSummary]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _round_cost(cost: float) -> float:
    """Round cost to 2 decimal places using round-half-up."""
    return float(Decimal(str(cost)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _paper_results_to_summaries(
    results: list[Any],
) -> list[PaperSummary]:
    """Convert PaperResult objects to serialisable dicts."""
    summaries: list[PaperSummary] = []
    # mode='json' coerces non-JSON-safe Pydantic field types (datetime, Path,
    # Decimal, Enum) into their JSON representations. Without it, Decimal
    # leaks as a Python object into json.dumps(..., default=str) and gets
    # serialised as the string "0.85" instead of a float, breaking
    # scripts/validate_pipeline.py and the fine-tune dataset builder.
    for r in results:
        genes_data = [g.model_dump(mode="json") for g in r.genes]
        rejected_data = [
            {"gene": rg.gene.model_dump(mode="json"), "reasons": rg.reasons}
            for rg in getattr(r, "rejected_genes", [])
        ]
        summary: PaperSummary = {
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
        # Include per-step timing when available
        for timing_field in ("pdf_parse_time", "llm_time", "validation_time"):
            val = getattr(r, timing_field, 0.0)
            if val > 0:
                summary[timing_field] = val
        summaries.append(summary)
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
    *,
    pipeline_config: dict[str, Any],
    provider: LLMProvider,
) -> PipelineRunData:
    """Assemble the fields shared by all three run-data builders."""
    tu = metrics.token_usage
    cost = provider.estimate_cost(tu, config)

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
            "thinking_tokens": tu.thinking_tokens,
            "text_output_tokens": tu.text_output_tokens,
            "cache_creation_input_tokens": tu.cache_creation_input_tokens,
            "cache_read_input_tokens": tu.cache_read_input_tokens,
            "total_tokens": tu.total_tokens,
            "cache_hit_rate": round(tu.cache_hit_rate, 4),
            "truncated_responses": tu.truncated_responses,
            "estimated_cost_usd": _round_cost(cost) if cost is not None else None,
        },
        "batch_validation_warnings": batch_warnings,
        "papers_detail": _paper_results_to_summaries(results),
    }


def build_run_data(
    metrics: PipelineMetrics,
    results: list[Any],
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
    provider = get_provider(config)
    data = _build_common_run_data(
        metrics,
        results,
        batch_warnings,
        config,
        total_duration,
        pipeline_config={
            **provider.report_metadata(config),
            "days_back": days_back,
            "dry_run": dry_run,
            "confidence_threshold": config.confidence_threshold,
        },
        provider=provider,
    )
    data["search"] = {
        "pmids_found": total_pmids_found,
        "pmids_new": new_pmids_count,
        "pmids_skipped": total_pmids_found - new_pmids_count,
    }
    data["database"] = gene_result
    return data


def _build_offline_run_data(
    metrics: PipelineMetrics,
    results: list[Any],
    batch_warnings: list[str],
    config: PipelineConfig,
    total_duration: float,
    extra_config: dict[str, Any],
) -> PipelineRunData:
    """Shared builder for local-PDF and PMID-list runs."""
    provider = get_provider(config)
    failed_count = sum(1 for r in results if not r.succeeded)
    pipeline_config = {
        **provider.report_metadata(config),
        "skip_validation": extra_config.get("skip_validation", False),
        "confidence_threshold": config.confidence_threshold,
        **extra_config,
    }
    data = _build_common_run_data(
        metrics,
        results,
        batch_warnings,
        config,
        total_duration,
        pipeline_config=pipeline_config,
        provider=provider,
    )
    data["papers"]["total"] = failed_count + metrics.papers_processed
    return data


def build_local_pdf_run_data(
    metrics: PipelineMetrics,
    results: list[Any],
    batch_warnings: list[str],
    config: PipelineConfig,
    pdf_dir: Path,
    skip_validation: bool,
    total_duration: float = 0.0,
) -> PipelineRunData:
    """Assemble run data for a local-PDF extraction run."""
    return _build_offline_run_data(
        metrics,
        results,
        batch_warnings,
        config,
        total_duration,
        {
            "mode": "local_pdf",
            "pdf_directory": str(pdf_dir),
            "skip_validation": skip_validation,
        },
    )


def build_pmid_run_data(
    metrics: PipelineMetrics,
    results: list[Any],
    batch_warnings: list[str],
    config: PipelineConfig,
    pmid_file: Path,
    skip_validation: bool,
    total_duration: float = 0.0,
) -> PipelineRunData:
    """Assemble run data for a PMID-list extraction run."""
    return _build_offline_run_data(
        metrics,
        results,
        batch_warnings,
        config,
        total_duration,
        {
            "mode": "pmid_list",
            "pmid_file": str(pmid_file),
            "skip_validation": skip_validation,
        },
    )


def write_comprehensive_report(data: PipelineRunData, log_dir: Path) -> Path:
    """Write the full run data as JSON.

    Args:
        data: PipelineRunData from build_run_data().
        log_dir: Directory for report files.

    Returns:
        Path to the written JSON file.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    # UTC so the filename stamp matches the body's `timestamp` field (also
    # UTC) regardless of host timezone. Otherwise the dashboard's Run
    # History shows two mismatched times for the same run.
    stamp = datetime.now(UTC).strftime("%Y-%m-%d_%Hh%Mm%Ss")
    path = log_dir / f"pipeline_report_{stamp}.json"
    path.write_text(json.dumps(data, indent=2, default=str) + "\n")
    return path


def _overview_lines(data: PipelineRunData) -> list[str]:
    """Build the overview panel lines for any run mode."""
    cfg = data.get("pipeline_config", {})
    papers = data.get("papers", {})
    run_mode = cfg.get("mode")
    mode = {
        "local_pdf": "LOCAL PDF",
        "pmid_list": "PMID LIST",
    }.get(run_mode, "DRY RUN" if cfg.get("dry_run") else "LIVE")

    lines = [
        f"[bold]Model:[/bold] {cfg.get('model', 'N/A')}",
        f"[bold]Mode:[/bold] {mode}",
        f"[bold]Total time:[/bold] {data.get('total_processing_time', 0):.1f}s",
        f"[bold]Total compute:[/bold] {data.get('total_compute_time', 0):.1f}s",
    ]

    if run_mode == "local_pdf":
        lines.extend(
            [
                f"[bold]Directory:[/bold] {cfg.get('pdf_directory', 'N/A')}",
                f"[bold]Validation:[/bold] "
                f"{'skipped' if cfg.get('skip_validation') else 'enabled'}",
                f"[bold]PDFs processed:[/bold] {papers.get('processed', 0)} "
                f"/ {papers.get('total', 0)}",
            ]
        )
    elif run_mode == "pmid_list":
        lines.extend(
            [
                f"[bold]File:[/bold] {cfg.get('pmid_file', 'N/A')}",
                f"[bold]Validation:[/bold] "
                f"{'skipped' if cfg.get('skip_validation') else 'enabled'}",
                f"[bold]Papers processed:[/bold] {papers.get('processed', 0)} "
                f"/ {papers.get('total', 0)}"
                f" ({papers.get('fulltext', 0)} fulltext, "
                f"{papers.get('abstract_only', 0)} abstract)",
            ]
        )
    else:
        search = data.get("search", {})
        lines.extend(
            [
                f"[bold]Days back:[/bold] {cfg.get('days_back', 'N/A')}",
                f"[bold]PMIDs found:[/bold] {search.get('pmids_found', 0)} "
                f"({search.get('pmids_new', 0)} new, "
                f"{search.get('pmids_skipped', 0)} skipped)",
                f"[bold]Papers processed:[/bold] {papers.get('processed', 0)} "
                f"({papers.get('fulltext', 0)} fulltext, "
                f"{papers.get('abstract_only', 0)} abstract)",
            ]
        )

    if papers.get("failed", 0) > 0:
        lines.append(f"[bold red]Papers failed:[/bold red] {papers['failed']}")

    return lines


def _new_table(title: str, title_style: str = "bold") -> Table:
    """Create a consistently styled summary table."""
    return Table(title=title, show_lines=True, title_style=title_style)


def _timing_breakdown(paper: PaperSummary) -> str:
    """Format available per-step paper timings."""
    parts = [
        f"{label}:{duration:.1f}s"
        for label, field in (
            ("pdf", "pdf_parse_time"),
            ("llm", "llm_time"),
            ("val", "validation_time"),
        )
        if (duration := paper.get(field, 0)) > 0
    ]
    return " ".join(parts)


def _print_papers_table(
    console: Console,
    papers_detail: list[PaperSummary],
    run_mode: Any,
) -> None:
    """Print per-paper status and timing details."""
    if not papers_detail:
        return

    table = _new_table("Papers")
    table.add_column("File" if run_mode == "local_pdf" else "PMID", style="bold")
    table.add_column("Source")
    table.add_column("Genes", justify="right")
    table.add_column("Time", justify="right")
    table.add_column("Breakdown", justify="right")
    table.add_column("Status")

    for paper in papers_detail:
        if error_message := paper.get("error"):
            style = "red"
            status = Text(error_message[:60], style="red")
        elif paper["gene_count"] > 0:
            style = "green"
            status = Text("OK", style="green")
        else:
            style = "yellow"
            status = Text("0 genes", style="yellow")

        table.add_row(
            paper["pmid"],
            paper.get("source", ""),
            str(paper["gene_count"]),
            f"{paper.get('processing_time', 0):.1f}s",
            _timing_breakdown(paper),
            status,
            style=style,
        )
    console.print(table)


def _confidence_style(confidence: float) -> str:
    """Map confidence to its terminal display color."""
    if confidence >= 0.9:
        return "green"
    if confidence >= 0.7:
        return "yellow"
    return "red"


def _print_genes_table(console: Console, papers_detail: list[PaperSummary]) -> None:
    """Print all accepted genes in the run."""
    genes = [gene for paper in papers_detail for gene in paper.get("genes", [])]
    if not genes:
        return

    table = _new_table("Extracted Genes")
    table.add_column("Gene", style="bold")
    table.add_column("Protein")
    table.add_column("PMID")
    table.add_column("Confidence", justify="right")
    table.add_column("GWAS Traits")
    table.add_column("MR", justify="center")
    table.add_column("Omics")

    for gene in genes:
        confidence = gene.get("confidence", 0)
        table.add_row(
            gene.get("gene_symbol", ""),
            gene.get("protein_name") or "",
            gene.get("pmid", ""),
            Text(f"{confidence:.2f}", style=_confidence_style(confidence)),
            ", ".join(gene.get("gwas_trait", [])),
            Text("✓", style="green")
            if gene.get("mendelian_randomization")
            else Text("✗", style="red"),
            ", ".join(gene.get("omics_evidence", [])),
        )
    console.print(table)


def _print_rejected_genes_table(
    console: Console, papers_detail: list[PaperSummary]
) -> None:
    """Print all rejected genes in the run."""
    rejected_genes = [
        rejected
        for paper in papers_detail
        for rejected in paper.get("rejected_genes", [])
    ]
    if not rejected_genes:
        return

    table = _new_table("Rejected Genes", "bold red")
    table.add_column("Gene", style="bold")
    table.add_column("Protein")
    table.add_column("PMID")
    table.add_column("Confidence", justify="right")
    table.add_column("Rejection Reasons")

    for rejected in rejected_genes:
        gene = rejected.get("gene", {})
        table.add_row(
            gene.get("gene_symbol", ""),
            gene.get("protein_name") or "",
            gene.get("pmid", ""),
            Text(f"{gene.get('confidence', 0):.2f}", style="red"),
            Text("; ".join(rejected.get("reasons", [])), style="dim"),
        )
    console.print(table)


def _print_validation_panel(console: Console, data: PipelineRunData) -> None:
    """Print validation totals and batch warnings."""
    genes = data.get("genes", {})
    papers = data.get("papers", {})
    lines = [
        f"[bold]Extracted genes:[/bold] {genes.get('extracted', 0)}",
        f"[bold]Validated genes:[/bold] {genes.get('validated', 0)}",
        f"[bold]Rejected genes:[/bold] {genes.get('rejected', 0)}",
        f"[bold]Acceptance rate:[/bold] {genes.get('acceptance_rate', 0):.1%}",
        f"[bold]Fulltext rate:[/bold] {papers.get('fulltext_rate', 0):.1%}",
    ]
    truncated = data.get("token_usage", {}).get("truncated_responses", 0)
    if truncated:
        lines.append(
            f"[bold red]Truncated responses:[/bold red] {truncated} "
            f"(raise PIPELINE_LLM_MAX_TOKENS)"
        )
    batch_warnings = data.get("batch_validation_warnings", [])
    if batch_warnings:
        lines.extend(
            (
                "",
                f"[bold yellow]Batch warnings ({len(batch_warnings)}):[/bold yellow]",
                *(f"  [yellow]- {warning}[/yellow]" for warning in batch_warnings),
            )
        )

    console.print(
        Panel(
            "\n".join(lines),
            title="[bold magenta]Validation[/bold magenta]",
            border_style="magenta",
        )
    )


def _print_token_panel(console: Console, data: PipelineRunData) -> None:
    """Print token usage and estimated cost when the run used tokens."""
    tu = data.get("token_usage", {})
    total = tu.get("total_tokens", 0)
    if total <= 0:
        return

    cost = tu.get("estimated_cost_usd")
    cost_str = f"${_round_cost(cost):.2f} USD" if cost is not None else "N/A"
    thinking = tu.get("thinking_tokens", 0)
    text_out = tu.get("text_output_tokens", 0)
    output_breakdown = (
        f" (~{thinking:,} thinking + ~{text_out:,} text)" if thinking > 0 else ""
    )
    lines = [
        f"[bold]Input tokens:[/bold] {tu.get('input_tokens', 0):,}",
        f"[bold]Output tokens:[/bold] {tu.get('output_tokens', 0):,}{output_breakdown}",
        f"[bold]Cache read:[/bold] {tu.get('cache_read_input_tokens', 0):,}",
        f"[bold]Cache created:[/bold] {tu.get('cache_creation_input_tokens', 0):,}",
        f"[bold]Cache hit rate:[/bold] {tu.get('cache_hit_rate', 0):.1%}",
        f"[bold]Total tokens used:[/bold] {total:,}",
        f"[bold]Estimated cost:[/bold] {cost_str}",
    ]
    console.print(
        Panel(
            "\n".join(lines),
            title="[bold blue]Tokens & Cost[/bold blue]",
            border_style="blue",
        )
    )


def _print_database_panel(console: Console, data: PipelineRunData) -> None:
    """Print database merge totals for online modes."""
    if data.get("pipeline_config", {}).get("mode") in {"local_pdf", "pmid_list"}:
        return

    database = data.get("database")
    lines = (
        [
            f"[bold]Inserted:[/bold] {database.get('inserted', 0)}",
            f"[bold]Updated:[/bold] {database.get('updated', 0)}",
        ]
        if database is not None
        else ["[dim]Dry run — no database writes[/dim]"]
    )
    console.print(
        Panel(
            "\n".join(lines),
            title="[bold green]Database[/bold green]",
            border_style="green",
        )
    )


def print_rich_summary(data: PipelineRunData) -> None:
    """Print a rich-formatted pipeline summary directly to the console.

    Uses its own Console instance to bypass the logging RichHandler,
    avoiding double-formatting of rich markup.
    """
    console = Console()
    console.print()
    console.print(
        Panel(
            "\n".join(_overview_lines(data)),
            title="[bold cyan]Pipeline Overview[/bold cyan]",
            border_style="cyan",
        )
    )

    papers_detail = data.get("papers_detail", [])
    run_mode = data.get("pipeline_config", {}).get("mode")
    _print_papers_table(console, papers_detail, run_mode)
    _print_genes_table(console, papers_detail)
    _print_rejected_genes_table(console, papers_detail)
    _print_validation_panel(console, data)
    _print_token_panel(console, data)
    _print_database_panel(console, data)
    console.print()
