"""Local-PDF extraction orchestration: parse → vllm → validate → report."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from pipeline.batch_validation import batch_validate
from pipeline.llm_providers.base import ExtractionFailedError, GeneEntry
from pipeline.pdf_retrieval import parse_local_pdf
from pipeline.quality_metrics import PipelineMetrics
from pipeline.rate_limiter import AsyncRateLimiter
from pipeline.report import (
    build_local_pdf_run_data,
    print_rich_summary,
    write_comprehensive_report,
)
from pipeline.validation import (
    close_validation_client,
    init_validation_state,
)

if TYPE_CHECKING:
    from pipeline.config import PipelineConfig

logger = logging.getLogger(__name__)


def _emit_stage(name: str) -> None:
    """Emit a `##STAGE:name##` marker that the runner subprocess parses."""
    print(f"##STAGE:{name}##", flush=True)


async def _process_pdf(
    pdf_path: Path,
    provider,
    config: PipelineConfig,
    rate_limiter: AsyncRateLimiter,
    metrics: PipelineMetrics,
    skip_validation: bool,
):
    """Parse one PDF, run extraction, validate, return a PaperResult."""
    from pipeline.main import PaperResult, RejectedGene, _validate_genes

    file_id = pdf_path.stem
    start = time.monotonic()
    try:
        parse_start = time.monotonic()
        text = await asyncio.to_thread(parse_local_pdf, pdf_path)
        parse_elapsed = time.monotonic() - parse_start

        if not text:
            return PaperResult(
                pmid=file_id,
                error="empty or corrupt PDF",
                processing_time=time.monotonic() - start,
                pdf_parse_time=parse_elapsed,
            )

        llm_start = time.monotonic()
        try:
            genes, usage = await provider.extract(text, file_id, config, rate_limiter)
        except ExtractionFailedError as exc:
            # A single PDF failing to extract must not bring down the whole
            # TaskGroup — that would cancel every other in-flight extraction
            # and abandon their tokens. Mirror pipeline/main.py: record token
            # usage from the failed attempt (when present) and return an
            # error-tagged PaperResult.
            if exc.token_usage is not None:
                metrics.token_usage += exc.token_usage
            return PaperResult(
                pmid=file_id,
                error=str(exc),
                processing_time=time.monotonic() - start,
                pdf_parse_time=parse_elapsed,
                llm_time=time.monotonic() - llm_start,
            )
        llm_elapsed = time.monotonic() - llm_start
        metrics.genes_extracted += len(genes)
        metrics.token_usage += usage
        for g in genes:
            g.pmid = file_id

        validate_start = time.monotonic()
        if skip_validation:
            validated: list[GeneEntry] = []
            rejected: list = []
            for g in genes:
                if g.confidence < config.confidence_threshold:
                    rejected.append(
                        RejectedGene(
                            gene=g,
                            reasons=[
                                f"Low confidence: {g.confidence:.2f} "
                                f"< {config.confidence_threshold}"
                            ],
                        )
                    )
                    metrics.genes_rejected += 1
                else:
                    validated.append(g)
                    metrics.genes_validated += 1
        else:
            validated, rejected = await _validate_genes(genes, metrics, config)
        validate_elapsed = time.monotonic() - validate_start

        metrics.papers_processed += 1
        metrics.fulltext_retrieved += 1

        return PaperResult(
            pmid=file_id,
            genes=validated,
            rejected_genes=rejected,
            fulltext=True,
            source="local_pdf",
            processing_time=time.monotonic() - start,
            pdf_parse_time=parse_elapsed,
            llm_time=llm_elapsed,
            validation_time=validate_elapsed,
        )
    except Exception as exc:
        logger.exception("Error processing %s", pdf_path.name)
        return PaperResult(
            pmid=file_id,
            error=str(exc),
            processing_time=time.monotonic() - start,
        )


async def run(
    provider,
    pdf_dir: Path,
    config: PipelineConfig,
    skip_validation: bool = False,
    log_dir: Path | None = None,
) -> Path:
    """Run the local-PDF extraction loop and write a JSON report.

    Returns:
        Path to the written report file.
    """
    if pdf_dir.is_file():
        if pdf_dir.suffix.lower() != ".pdf":
            raise ValueError(f"Not a PDF file: {pdf_dir}")
        pdf_files = [pdf_dir]
        pdf_dir = pdf_dir.parent
    elif pdf_dir.is_dir():
        pdf_files = sorted(pdf_dir.glob("*.pdf"))
    else:
        raise FileNotFoundError(f"Path not found: {pdf_dir}")
    if not pdf_files:
        raise ValueError(f"No .pdf files found in {pdf_dir}")

    if log_dir is None:
        log_dir = Path("logs")
    (log_dir / "json").mkdir(parents=True, exist_ok=True)

    metrics = PipelineMetrics()
    rate_limiter = AsyncRateLimiter(rpm=config.rpm_limit, tpm=config.tpm_limit)
    init_validation_state(config)

    pipeline_start = time.monotonic()
    semaphore = asyncio.Semaphore(config.max_concurrent_papers)
    progress = {"current": 0, "total": len(pdf_files)}

    async def _bounded(pdf_path: Path):
        async with semaphore:
            progress["current"] += 1
            logger.info(
                "[%d/%d] %s",
                progress["current"],
                progress["total"],
                pdf_path.name,
            )
            return await _process_pdf(
                pdf_path,
                provider,
                config,
                rate_limiter,
                metrics,
                skip_validation,
            )

    # PDF parse, LLM extraction, and NCBI validation all interleave per-PDF
    # inside the TaskGroup, so a sequential stage tracker can't represent
    # them honestly. Collapse the per-PDF phases into a single "extract"
    # stage and surface batch_validate / report afterwards.
    _emit_stage("extract")
    try:
        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(_bounded(p)) for p in pdf_files]
        results = [t.result() for t in tasks]

        all_genes: list[GeneEntry] = []
        for r in results:
            if r.succeeded:
                all_genes.extend(r.genes)

        _emit_stage("batch_validate")
        batch_warnings = batch_validate(all_genes) if all_genes else []
        for w in batch_warnings:
            logger.warning("Batch check: %s", w)

        _emit_stage("report")
        total_duration = time.monotonic() - pipeline_start
        run_data = build_local_pdf_run_data(
            metrics,
            results,
            batch_warnings,
            config,
            pdf_dir,
            skip_validation,
            total_duration,
        )
        # Inject vllm-aware metadata when the provider exposes it.
        # PipelineRunData is a TypedDict (dict subclass), so use dict syntax.
        if hasattr(provider, "report_metadata"):
            run_data["model_metadata"] = provider.report_metadata(config)  # ty: ignore[invalid-key]
        report_path = write_comprehensive_report(run_data, log_dir / "json")
        print_rich_summary(run_data)
        print(f"REPORT_PATH={report_path}", flush=True)
        return report_path
    finally:
        await close_validation_client()
        await provider.close()
