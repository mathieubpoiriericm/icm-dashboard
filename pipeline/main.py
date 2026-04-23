#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
"""
Main entry point for the SVD Dashboard data pipeline.

Runs one or more of three independently-selectable pipelines:

- PubMed gene extraction (``--pubmed``, also the default when no flag is set)
- ClinicalTrials.gov fetch (``--clinical-trials``)
- External metadata enrichment: NCBI Gene, UniProt, PubMed citations
  (``--sync-external-data``)

Flags can be combined; selected pipelines run in sequence with a single
healthcheck ping and notification per invocation.

Usage:
    python pipeline/main.py [--days-back N] [--dry-run] [--test-mode]
    python pipeline/main.py --clinical-trials
    python pipeline/main.py --pubmed --clinical-trials
    python pipeline/main.py --sync-external-data
    python pipeline/main.py --local-pdfs PATH [--skip-validation]
    python pipeline/main.py --pmids FILE [--skip-validation]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser (stdlib-only, no heavy imports)."""
    parser = argparse.ArgumentParser(description="SVD Dashboard data pipeline")
    parser.add_argument(
        "--days-back",
        type=int,
        default=7,
        help="Number of days to look back for new papers (default: 7)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without writing to database",
    )
    parser.add_argument(
        "--test-mode",
        action="store_true",
        help=(
            "Run without LLM extraction or database merge"
            " (for testing search/retrieval only)"
        ),
    )
    parser.add_argument(
        "--pubmed",
        action="store_true",
        help=(
            "Explicitly run the PubMed gene extraction pipeline."
            " Also runs by default when no pipeline selector flag is given."
        ),
    )
    parser.add_argument(
        "--clinical-trials",
        action="store_true",
        help="Run the ClinicalTrials.gov discovery pipeline.",
    )
    parser.add_argument(
        "--sync-external-data",
        action="store_true",
        help=(
            "Sync external metadata (NCBI Gene, UniProt, PubMed citations)"
            " for all genes in the database. Clinical trial discovery is"
            " a separate pipeline (use --clinical-trials)."
        ),
    )
    parser.add_argument(
        "--local-pdfs",
        type=Path,
        metavar="PATH",
        help="Extract genes from a local PDF file or directory of PDFs"
        " (no PubMed search or database)",
    )
    parser.add_argument(
        "--pmids",
        type=Path,
        metavar="FILE",
        help="Process specific PMIDs from a text file (one per line, no database)",
    )
    parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="Skip NCBI gene validation (only valid with --local-pdfs or --pmids)",
    )
    parser.add_argument(
        "--llm-provider",
        # Kept in sync with pipeline.config.LLMProviderName manually — importing
        # LLM_PROVIDERS here would pull in lxml via pipeline.config and break
        # the stdlib-only fast path used for argcomplete below.
        choices=["anthropic", "ollama"],
        default=None,
        help="Override PIPELINE_LLM_PROVIDER for this run.",
    )
    parser.add_argument(
        "--ollama-model",
        default=None,
        help="Override PIPELINE_OLLAMA_MODEL for this run (e.g. svd-gemma:v1).",
    )
    return parser


# --- Fast path for tab-completion ---
# argcomplete.autocomplete() calls sys.exit() during completion,
# so heavy imports below never load. This keeps <TAB> instant.
if __name__ == "__main__":
    try:
        import argcomplete

        _parser = _build_parser()
        argcomplete.autocomplete(_parser)
        del _parser
    except ImportError:
        pass
# --- End fast path ---

import asyncio
import json
import logging
import time
import traceback
from collections.abc import Awaitable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final, TypedDict

import asyncpg
import httpx
from lxml import etree  # type: ignore[import-untyped]

# Add project root to path for imports when running as script
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load environment variables from .env file
from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

import os

# macOS Python framework builds may lack a default CA bundle at the compiled-in
# OpenSSL path.  When SSL_CERT_FILE is not already set, point it at the certifi
# bundle so that urllib/httpx/etc. can verify TLS certificates out of the box.
if not os.environ.get("SSL_CERT_FILE"):
    try:
        import certifi

        os.environ["SSL_CERT_FILE"] = certifi.where()
    except ImportError:
        pass

from pipeline.batch_validation import batch_validate
from pipeline.clinical_trials_fetch import close_ctg_client, sync_clinical_trials
from pipeline.config import (
    NCBI_EFETCH_URL,
    SAFE_XML_PARSER,
    PipelineConfig,
    validate_pmid,
)
from pipeline.data_merger import merge_gene_entries
from pipeline.database import (
    Database,
    get_existing_pmids,
    record_pipeline_run,
    record_processed_pmids_batch,
    reset_sequence,
)
from pipeline.event_log import EventLog
from pipeline.healthcheck import (
    close_healthcheck_client,
    ping_failure,
    ping_start,
    ping_success,
)
from pipeline.http_client import AsyncHttpClientManager
from pipeline.llm_extraction import GeneEntry, close_async_client, extract_from_paper
from pipeline.ncbi_gene_fetch import init_ncbi_fetch_state
from pipeline.notifications import send_pipeline_notification
from pipeline.pdf_retrieval import (
    close_http_client,
    get_fulltext,
    parse_local_pdf,
)
from pipeline.pubmed_search import filter_new_pmids, search_recent_papers
from pipeline.quality_metrics import PipelineMetrics, TokenUsage
from pipeline.rate_limiter import AsyncRateLimiter
from pipeline.report import (
    PipelineRunData,
    build_local_pdf_run_data,
    build_pmid_run_data,
    build_run_data,
    print_rich_summary,
    write_comprehensive_report,
)
from pipeline.validation import (
    clear_gene_cache,
    close_validation_client,
    init_validation_state,
    validate_gene_entry,
)

# --- Constants ---
LOG_SEPARATOR: Final[str] = "=" * 50
# Configure logging
LOG_DIR = Path(os.getenv("PIPELINE_LOG_DIR", PROJECT_ROOT / "logs"))
LOG_DIR.mkdir(exist_ok=True)
LOG_LOG_DIR = LOG_DIR / "log"
LOG_LOG_DIR.mkdir(exist_ok=True)
# UTC matches every other timestamp in the pipeline; PID suffix survives
# same-second invocations (e.g. scheduler overlap, manual re-runs).
LOG_FILE = LOG_LOG_DIR / (
    f"pipeline_{datetime.now(UTC).strftime('%Y-%m-%d_%Hh%Mm%Ss')}_{os.getpid()}.log"
)

from rich.logging import RichHandler

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[
        RichHandler(
            rich_tracebacks=True,
            markup=True,
            show_path=False,
        ),
        logging.FileHandler(LOG_FILE),
    ],
)
# Keep file handler plain-text (no ANSI codes)
logging.getLogger().handlers[1].setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
)
logger = logging.getLogger(__name__)


# -------------------------------------------------------------------------
# PIPELINE PROGRESS REPORTING
# -------------------------------------------------------------------------
_STAGES: Final[tuple[tuple[str, str], ...]] = (
    ("searching_pubmed", "Searching PubMed"),
    ("filtering_pmids", "Filtering already-processed papers"),
    ("processing_papers", "Processing papers"),
    ("batch_validation", "Running batch quality checks"),
    ("merging_database", "Merging validated data into database"),
    ("finalizing", "Recording results and finalizing"),
)
_TOTAL_STAGES: Final[int] = len(_STAGES)


def _write_progress(
    config: PipelineConfig,
    *,
    status: str,
    stage: str,
    stage_label: str,
    stage_number: int,
    started_at: str | None = None,
    error_message: str | None = None,
    run_mode: str = "standard",
) -> None:
    """Write pipeline progress to JSON for dashboard consumption.

    Uses atomic write (tmp + rename) so readers never see partial data.
    Logs but does not raise on write failures to avoid disrupting the pipeline.
    """
    progress_path = Path(config.progress_file)
    tmp_path = progress_path.with_suffix(".tmp")
    data = {
        "status": status,
        "stage": stage,
        "stage_label": stage_label,
        "stage_number": stage_number,
        "total_stages": _TOTAL_STAGES,
        "started_at": started_at,
        "updated_at": datetime.now(tz=UTC).isoformat(),
        "error_message": error_message,
        "run_mode": run_mode,
    }
    try:
        tmp_path.write_text(json.dumps(data) + "\n")
        os.replace(str(tmp_path), str(progress_path))
    except OSError:
        logger.debug("Failed to write progress file %s", progress_path, exc_info=True)


# --- Type definitions ---
class MetadataResult(TypedDict):
    """Result from metadata fetch."""

    pmid: str
    doi: str | None


@dataclass(slots=True)
class RejectedGene:
    """A gene that failed validation, preserved for reporting."""

    gene: GeneEntry
    reasons: list[str]


class PaperProcessResult(TypedDict):
    """Result from processing a single paper."""

    genes: list[GeneEntry]
    rejected_genes: list[RejectedGene]
    fulltext: bool
    source: str


@dataclass(slots=True)
class PaperResult:
    """Result from processing a single paper with error handling."""

    pmid: str
    genes: list[GeneEntry] = field(default_factory=list)
    rejected_genes: list[RejectedGene] = field(default_factory=list)
    fulltext: bool = False
    source: str = "none"
    error: str | None = None
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    processing_time: float = 0.0
    pdf_parse_time: float = 0.0
    llm_time: float = 0.0
    validation_time: float = 0.0

    @property
    def succeeded(self) -> bool:
        return self.error is None


async def _validate_genes(
    genes: list[GeneEntry],
    metrics: PipelineMetrics,
    config: PipelineConfig,
) -> tuple[list[GeneEntry], list[RejectedGene]]:
    """Validate genes concurrently against NCBI and return (valid, rejected) lists."""
    validated_genes: list[GeneEntry] = []
    rejected_genes: list[RejectedGene] = []
    validation_tasks = [validate_gene_entry(gene, config=config) for gene in genes]
    results = await asyncio.gather(*validation_tasks, return_exceptions=True)

    for gene, result in zip(genes, results, strict=True):
        # gather(return_exceptions=True) can yield BaseException (e.g. CancelledError),
        # not just Exception — narrow on the wider type so downstream attribute
        # access on the ValidationResult branch is type-safe.
        if isinstance(result, BaseException):
            logger.error(f"  Validation error for {gene.gene_symbol}: {result}")
            metrics.genes_rejected += 1
            rejected_genes.append(RejectedGene(gene=gene, reasons=[str(result)]))
        elif result.is_valid and result.normalized_data is not None:
            validated_genes.append(result.normalized_data)
            metrics.genes_validated += 1
        else:
            metrics.genes_rejected += 1
            logger.debug(f"  Gene rejected: {result.errors}")
            rejected_genes.append(RejectedGene(gene=gene, reasons=result.errors))

    return validated_genes, rejected_genes


# --- Shared HTTP client for metadata ---
# AsyncHttpClientManager serialises lazy init under an asyncio.Lock so the
# first wave of concurrent paper-processing tasks doesn't each build (and
# leak) its own httpx.AsyncClient.
_metadata_client_manager = AsyncHttpClientManager(
    timeout=httpx.Timeout(30.0),
    limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
)


async def _get_metadata_client() -> httpx.AsyncClient:
    """Get or create shared HTTP client for metadata fetching."""
    return await _metadata_client_manager.get()


async def _close_metadata_client() -> None:
    """Close shared metadata HTTP client."""
    await _metadata_client_manager.close()


async def fetch_paper_metadata(pmid: str) -> MetadataResult:
    """Fetch DOI and other metadata for a PMID using NCBI efetch.

    Args:
        pmid: PubMed ID.

    Returns:
        MetadataResult with pmid and doi.
    """
    pmid = validate_pmid(pmid)

    url = NCBI_EFETCH_URL
    params: dict[str, str] = {"db": "pubmed", "id": pmid, "retmode": "xml"}

    if api_key := os.getenv("NCBI_API_KEY"):
        params["api_key"] = api_key

    try:
        client = await _get_metadata_client()
        resp = await client.get(url, params=params)

        if resp.status_code != 200:
            logger.warning(f"Metadata fetch failed for PMID {pmid}: {resp.status_code}")
            return {"pmid": pmid, "doi": None}

        root = etree.fromstring(resp.content, parser=SAFE_XML_PARSER)
        doi_elem = root.find(".//ArticleId[@IdType='doi']")
        doi = doi_elem.text if doi_elem is not None else None
        return {"pmid": pmid, "doi": doi}

    except httpx.TimeoutException:
        logger.warning(f"Timeout fetching metadata for PMID {pmid}")
        return {"pmid": pmid, "doi": None}
    except httpx.RequestError as e:
        logger.warning(f"Request error fetching metadata for PMID {pmid}: {e}")
        return {"pmid": pmid, "doi": None}
    except etree.XMLSyntaxError as e:
        logger.error(f"XML parsing failed for PMID {pmid}: {e}")
        return {"pmid": pmid, "doi": None}


async def _record_and_notify(config: PipelineConfig, run_data: Any) -> None:
    """Record pipeline run to event log and send a notification.

    Offloads the blocking SQLite + Apprise work to a worker thread so the
    asyncio event loop isn't stalled during the final flush. Healthcheck
    pings are handled separately by the caller so they don't double-fire
    across multi-pipeline invocations.
    """

    def _run() -> None:
        with EventLog(config.event_db_path) as event_log:
            event_id = event_log.record("pipeline_completed", run_data)
            send_pipeline_notification(run_data, config)
            event_log.mark_notified([event_id])

    await asyncio.to_thread(_run)


async def _finalize_run(
    metrics: PipelineMetrics,
    run_data: PipelineRunData,
    run_mode: str,
) -> None:
    """Record run stats to database.

    Notification and healthcheck pings are handled separately by the
    caller (see ``main()``) so they can be coalesced across multiple
    pipelines in one invocation.
    """
    await record_pipeline_run(
        run_timestamp=run_data["timestamp"],
        papers_processed=metrics.papers_processed,
        fulltext_retrieved=metrics.fulltext_retrieved,
        genes_extracted=metrics.genes_extracted,
        genes_validated=metrics.genes_validated,
        run_mode=run_mode,
    )


async def process_paper(
    pmid: str,
    metrics: PipelineMetrics,
    config: PipelineConfig,
    rate_limiter: AsyncRateLimiter | None = None,
) -> PaperProcessResult:
    """Process a single paper: fetch text, extract data, validate.

    Args:
        pmid: PubMed ID.
        metrics: Metrics accumulator.
        config: Pipeline configuration.
        rate_limiter: Optional rate limiter for LLM calls.

    Returns:
        PaperProcessResult with genes, fulltext flag, and source.
    """
    logger.info(f"Processing PMID {pmid}")

    # Get DOI for Unpaywall lookup
    metadata = await fetch_paper_metadata(pmid)
    doi = metadata.get("doi")

    # Retrieve full text or abstract
    text_result = await get_fulltext(pmid, doi)

    text = text_result.get("text")
    if not text:
        logger.warning(f"  No text available for PMID {pmid}, skipping")
        return {"genes": [], "rejected_genes": [], "fulltext": False, "source": "none"}

    if text_result["fulltext"]:
        metrics.fulltext_retrieved += 1
        logger.info(f"  Retrieved full text from {text_result['source']}")
    else:
        metrics.abstract_only += 1
        logger.info("  Using abstract only")

    # Extract structured data using LLM (returns typed GeneEntry instances)
    genes, token_usage = await extract_from_paper(
        text, pmid, config=config, rate_limiter=rate_limiter
    )
    metrics.genes_extracted += len(genes)
    metrics.token_usage += token_usage

    logger.info(f"  Extracted {len(genes)} genes")

    # Set pmid on each gene for downstream tracking
    for gene in genes:
        gene.pmid = pmid

    # Validate genes concurrently
    validated_genes, rejected_genes = await _validate_genes(genes, metrics, config)

    return {
        "genes": validated_genes,
        "rejected_genes": rejected_genes,
        "fulltext": text_result["fulltext"],
        "source": text_result["source"],
    }


async def process_paper_safe(
    pmid: str,
    metrics: PipelineMetrics,
    semaphore: asyncio.Semaphore,
    progress: dict[str, int],
    config: PipelineConfig,
    rate_limiter: AsyncRateLimiter | None = None,
) -> PaperResult:
    """Process a single paper with error handling and concurrency control.

    Args:
        pmid: PubMed ID.
        metrics: Metrics accumulator.
        semaphore: Semaphore for concurrency control.
        progress: Shared dict with 'current' counter and 'total' count.
        config: Pipeline configuration.
        rate_limiter: Optional rate limiter for LLM calls.

    Returns:
        PaperResult with processing outcome.
    """
    async with semaphore:
        progress["current"] += 1
        current = progress["current"]
        total = progress["total"]
        logger.info(f"[{current}/{total}] Starting PMID {pmid}")
        start_time = time.monotonic()
        try:
            result = await process_paper(
                pmid, metrics, config=config, rate_limiter=rate_limiter
            )
            duration = time.monotonic() - start_time
            return PaperResult(
                pmid=pmid,
                genes=result["genes"],
                rejected_genes=result["rejected_genes"],
                fulltext=result["fulltext"],
                source=result["source"],
                processing_time=duration,
            )
        except Exception as e:
            logger.exception(f"Error processing PMID {pmid}")
            duration = time.monotonic() - start_time
            return PaperResult(pmid=pmid, error=str(e), processing_time=duration)


async def process_papers_concurrently(
    pmids: list[str],
    metrics: PipelineMetrics,
    config: PipelineConfig,
    rate_limiter: AsyncRateLimiter | None = None,
) -> list[PaperResult]:
    """Process multiple papers concurrently with bounded concurrency.

    Args:
        pmids: List of PubMed IDs.
        metrics: Metrics accumulator.
        config: Pipeline configuration.
        rate_limiter: Optional rate limiter for LLM calls.

    Returns:
        List of PaperResult for each paper.
    """
    semaphore = asyncio.Semaphore(config.max_concurrent_papers)
    progress = {"current": 0, "total": len(pmids)}

    async with asyncio.TaskGroup() as tg:
        tasks = [
            tg.create_task(
                process_paper_safe(
                    pmid,
                    metrics,
                    semaphore,
                    progress,
                    config=config,
                    rate_limiter=rate_limiter,
                )
            )
            for pmid in pmids
        ]

    return [task.result() for task in tasks]


async def run_pipeline(
    days_back: int = 7,
    dry_run: bool = False,
    test_mode: bool = False,
    config: PipelineConfig | None = None,
    manage_lifecycle: bool = True,
) -> tuple[PipelineMetrics, PipelineRunData | None]:
    """Run the PubMed gene extraction pipeline.

    Args:
        days_back: Number of days to look back (1-3650).
        dry_run: If True, skip database writes.
        test_mode: If True, skip LLM extraction.
        config: Pipeline configuration (uses defaults if None).
        manage_lifecycle: When True (default, for direct callers and tests),
            this function handles its own healthcheck pings and notification.
            When False (set by the ``main()`` dispatcher for combined runs),
            pings and notifications are skipped so the dispatcher can coalesce
            them across pipelines.

    Returns:
        A tuple of (PipelineMetrics, run_data). ``run_data`` is ``None`` on
        early exits where no pipeline summary is built (no papers found, all
        PMIDs already processed, test-mode preview).

    Raises:
        ValueError: If days_back is out of valid range.
    """
    if config is None:
        config = PipelineConfig()

    # Input validation
    if not config.min_days_back <= days_back <= config.max_days_back:
        raise ValueError(
            f"days_back must be between {config.min_days_back} "
            f"and {config.max_days_back}, got {days_back}"
        )

    metrics = PipelineMetrics()

    # Set up database config
    Database.set_config(config)

    # Eagerly initialize async locks/semaphores (safe under free-threading)
    init_validation_state(config)
    init_ncbi_fetch_state(config)

    # Set up rate limiter
    rate_limiter = AsyncRateLimiter(rpm=config.rpm_limit, tpm=config.tpm_limit)

    pipeline_start_time = time.monotonic()
    if manage_lifecycle:
        await ping_start(config.healthcheck_url)

    logger.info(f"Starting SVD Dashboard pipeline (looking back {days_back} days)")
    model_display = (
        config.ollama_model if config.llm_provider == "ollama" else config.llm_model
    )
    logger.info(
        f"Config: model={model_display}, provider={config.llm_provider}, "
        f"concurrency={config.max_concurrent_papers}, "
        f"RPM={config.rpm_limit}, TPM={config.tpm_limit}"
    )

    progress_started_at = datetime.now(tz=UTC).isoformat()
    stage_idx = 0
    Path(config.progress_file).parent.mkdir(parents=True, exist_ok=True)

    def _report_stage(idx: int) -> None:
        nonlocal stage_idx
        stage_idx = idx
        sid, slabel = _STAGES[idx]
        _write_progress(
            config,
            status="running",
            stage=sid,
            stage_label=slabel,
            stage_number=idx + 1,
            started_at=progress_started_at,
        )

    try:
        # Step 1: Search PubMed for recent papers
        _report_stage(0)
        print("##STAGE:search##", flush=True)
        logger.info("Step 1: Searching PubMed for recent SVD genetic papers...")
        all_pmids = await search_recent_papers(days_back)
        logger.info(f"  Found {len(all_pmids)} papers matching SVD genetic criteria")

        if not all_pmids:
            logger.info("No new papers found. Pipeline complete.")
            return metrics, None

        # Step 2: Filter out already-processed papers
        _report_stage(1)
        print("##STAGE:retrieve##", flush=True)
        logger.info("Step 2: Filtering already-processed papers...")
        if dry_run or test_mode:
            existing_pmids: set[str] = set()
            logger.info("  Skipping PMID deduplication (dry-run/test mode)")
        else:
            try:
                existing_pmids = await get_existing_pmids()
            except asyncpg.UndefinedTableError:
                # First run only. Other DB errors must propagate — swallowing
                # them disables dedup and re-spends the LLM budget.
                logger.warning(
                    "  pubmed_refs table missing; treating as empty (first run?)"
                )
                existing_pmids = set()

        new_pmids = filter_new_pmids(all_pmids, existing_pmids)
        logger.info(f"  {len(new_pmids)} new papers to process")

        if not new_pmids:
            logger.info("All papers already processed. Pipeline complete.")
            return metrics, None

        # Test mode: skip LLM extraction and database merge
        if test_mode:
            logger.info(
                "Test mode enabled - skipping LLM extraction and database merge"
            )
            logger.info(f"  Would process {len(new_pmids)} papers:")
            for pmid in new_pmids[: config.test_mode_preview_count]:
                logger.info(f"    PMID: {pmid}")
            if len(new_pmids) > config.test_mode_preview_count:
                logger.info(
                    f"    ... and "
                    f"{len(new_pmids) - config.test_mode_preview_count}"
                    f" more"
                )
            return metrics, None

        # Step 3: Process papers concurrently
        _report_stage(2)
        print("##STAGE:extract##", flush=True)
        logger.info("Step 3: Processing papers concurrently...")
        results = await process_papers_concurrently(
            new_pmids, metrics, config=config, rate_limiter=rate_limiter
        )

        all_genes: list[GeneEntry] = []
        successful_results: list[PaperResult] = []
        for result in results:
            if result.succeeded:
                all_genes.extend(result.genes)
                metrics.papers_processed += 1
                successful_results.append(result)

        logger.info(f"  Processed {metrics.papers_processed} papers")
        logger.info(f"  Validated: {metrics.genes_validated} genes")

        # Step 3.5: Batch validation (warning-only quality checks)
        _report_stage(3)
        print("##STAGE:validate##", flush=True)
        batch_warnings: list[str] = []
        if all_genes:
            batch_warnings = batch_validate(all_genes)
            for warning in batch_warnings:
                logger.warning(f"  Batch check: {warning}")

        if dry_run:
            logger.info("Dry run mode - skipping database merge")
            _write_progress(
                config,
                status="completed",
                stage=_STAGES[stage_idx][0],
                stage_label="Pipeline completed (dry run)",
                stage_number=stage_idx + 1,
                started_at=progress_started_at,
            )

            total_duration = time.monotonic() - pipeline_start_time
            run_data = build_run_data(
                metrics,
                results,
                None,
                batch_warnings,
                config,
                days_back,
                dry_run,
                len(all_pmids),
                len(new_pmids),
                total_duration,
            )
            report_path = write_comprehensive_report(run_data, LOG_DIR / "json")
            logger.info(f"JSON report written to: {report_path}")
            print_rich_summary(run_data)

            if manage_lifecycle:
                await _record_and_notify(config, run_data)
                await ping_success(config.healthcheck_url)

            return metrics, run_data

        # Step 4: Merge into database
        _report_stage(4)
        print("##STAGE:merge##", flush=True)
        logger.info("Step 4: Merging validated data into database...")

        # Reset sequences to avoid primary key conflicts
        await reset_sequence("genes")

        gene_result = None
        if all_genes:
            gene_result = await merge_gene_entries(all_genes)
            logger.info(
                f"  Genes: {gene_result['inserted']} inserted, "
                f"{gene_result['updated']} updated"
            )

        # Step 5: Record processed PMIDs AFTER successful merge
        # This ensures PMIDs are only marked processed when genes are
        # actually written, preventing data loss on merge failure.
        pmid_records = [
            (r.pmid, r.fulltext, r.source, len(r.genes)) for r in successful_results
        ]
        recorded = await record_processed_pmids_batch(pmid_records)
        logger.info(f"  Recorded {recorded} processed PMIDs")

        # Finalize
        _report_stage(5)
        print("##STAGE:sync##", flush=True)

        # Comprehensive report + rich summary
        total_duration = time.monotonic() - pipeline_start_time
        run_data = build_run_data(
            metrics,
            results,
            gene_result,
            batch_warnings,
            config,
            days_back,
            dry_run,
            len(all_pmids),
            len(new_pmids),
            total_duration,
        )
        report_path = write_comprehensive_report(run_data, LOG_DIR / "json")
        logger.info(f"JSON report written to: {report_path}")
        print_rich_summary(run_data)

        await _finalize_run(metrics, run_data, "standard")
        if manage_lifecycle:
            await _record_and_notify(config, run_data)
            await ping_success(config.healthcheck_url)

        _write_progress(
            config,
            status="completed",
            stage=_STAGES[-1][0],
            stage_label="Pipeline completed successfully",
            stage_number=_TOTAL_STAGES,
            started_at=progress_started_at,
        )

        return metrics, run_data

    except Exception:
        sid, slabel = _STAGES[stage_idx]
        _write_progress(
            config,
            status="error",
            stage=sid,
            stage_label=f"Failed at: {slabel}",
            stage_number=stage_idx + 1,
            started_at=progress_started_at,
            error_message=traceback.format_exc()[:500],
        )
        if manage_lifecycle:
            await ping_failure(config.healthcheck_url, traceback.format_exc())
        raise

    finally:
        # Cleanup shared resources used only by this pipeline. The DB pool
        # is kept open for subsequent pipelines when the dispatcher is
        # managing the lifecycle — it closes the pool itself after all
        # selected pipelines have run.
        await _close_metadata_client()
        await close_http_client()
        await close_validation_client()
        await close_async_client()
        if manage_lifecycle:
            await close_healthcheck_client()
            await Database.close()
        clear_gene_cache()


async def run_local_pdf_pipeline(
    pdf_dir: Path,
    skip_validation: bool = False,
    config: PipelineConfig | None = None,
) -> None:
    """Run LLM extraction on local PDF files (no database, no PubMed search).

    Results are written as a JSON report and printed as a rich console summary.

    Args:
        pdf_dir: Path to a single .pdf file or a directory containing .pdf files.
        skip_validation: If True, skip NCBI gene validation.
        config: Pipeline configuration (uses defaults if None).

    Raises:
        FileNotFoundError: If pdf_dir does not exist.
        ValueError: If path is not a .pdf file, or directory contains no .pdf files.
    """
    if config is None:
        config = PipelineConfig()

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

    metrics = PipelineMetrics()
    rate_limiter = AsyncRateLimiter(rpm=config.rpm_limit, tpm=config.tpm_limit)

    init_validation_state(config)
    init_ncbi_fetch_state(config)

    pipeline_start_time = time.monotonic()
    await ping_start(config.healthcheck_url)

    logger.info(f"Starting local PDF pipeline: {len(pdf_files)} files in {pdf_dir}")
    model_display = (
        config.ollama_model if config.llm_provider == "ollama" else config.llm_model
    )
    logger.info(
        f"Config: model={model_display}, provider={config.llm_provider}, "
        f"validation={'disabled' if skip_validation else 'enabled'}"
    )

    results: list[PaperResult] = []
    all_genes: list[GeneEntry] = []

    try:
        semaphore = asyncio.Semaphore(config.max_concurrent_papers)
        progress = {"current": 0, "total": len(pdf_files)}

        async def _process_pdf(pdf_path: Path) -> PaperResult:
            file_id = pdf_path.stem
            async with semaphore:
                progress["current"] += 1
                current = progress["current"]
                total = progress["total"]
                logger.info(f"[{current}/{total}] Processing {pdf_path.name}")

                start_time = time.monotonic()
                try:
                    # Extract text
                    # Use asyncio.to_thread to avoid blocking loop with PDF parsing
                    pdf_parse_start = time.monotonic()
                    text = await asyncio.to_thread(parse_local_pdf, pdf_path)
                    pdf_parse_elapsed = time.monotonic() - pdf_parse_start

                    if not text:
                        logger.warning(f"  No text extracted from {pdf_path.name}")
                        return PaperResult(
                            pmid=file_id,
                            error="empty or corrupt PDF",
                            processing_time=time.monotonic() - start_time,
                            pdf_parse_time=pdf_parse_elapsed,
                        )

                    # LLM extraction
                    llm_start = time.monotonic()
                    genes, token_usage = await extract_from_paper(
                        text, file_id, config=config, rate_limiter=rate_limiter
                    )
                    llm_elapsed = time.monotonic() - llm_start

                    # Update metrics safely (single-threaded event loop)
                    metrics.genes_extracted += len(genes)
                    metrics.token_usage += token_usage

                    # Set identifier
                    for gene in genes:
                        gene.pmid = file_id

                    logger.info(f"  Extracted {len(genes)} genes from {pdf_path.name}")

                    # Validation
                    validation_start = time.monotonic()
                    if skip_validation:
                        # Still apply confidence threshold (cheap, local check)
                        validated_genes = []
                        rejected_genes: list[RejectedGene] = []
                        for gene in genes:
                            if gene.confidence < config.confidence_threshold:
                                rejected_genes.append(
                                    RejectedGene(
                                        gene=gene,
                                        reasons=[
                                            f"Low confidence: {gene.confidence:.2f}"
                                            f" < {config.confidence_threshold}"
                                        ],
                                    )
                                )
                                metrics.genes_rejected += 1
                            else:
                                validated_genes.append(gene)
                                metrics.genes_validated += 1
                    else:
                        validated_genes, rejected_genes = await _validate_genes(
                            genes, metrics, config
                        )
                    validation_elapsed = time.monotonic() - validation_start

                    metrics.papers_processed += 1
                    metrics.fulltext_retrieved += 1

                    return PaperResult(
                        pmid=file_id,
                        genes=validated_genes,
                        rejected_genes=rejected_genes,
                        fulltext=True,
                        source="local_pdf",
                        processing_time=time.monotonic() - start_time,
                        pdf_parse_time=pdf_parse_elapsed,
                        llm_time=llm_elapsed,
                        validation_time=validation_elapsed,
                    )

                except Exception as e:
                    logger.exception(f"Error processing {pdf_path.name}")
                    return PaperResult(
                        pmid=file_id,
                        error=str(e),
                        processing_time=time.monotonic() - start_time,
                    )

        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(_process_pdf(pdf_path)) for pdf_path in pdf_files]

        results = [task.result() for task in tasks]

        for result in results:
            if result.succeeded:
                all_genes.extend(result.genes)

        # Batch validation (warning-only)
        batch_warnings: list[str] = []
        if all_genes:
            batch_warnings = batch_validate(all_genes)
            for warning in batch_warnings:
                logger.warning(f"  Batch check: {warning}")

        # Report
        total_duration = time.monotonic() - pipeline_start_time
        run_data = build_local_pdf_run_data(
            metrics,
            results,
            batch_warnings,
            config,
            pdf_dir,
            skip_validation,
            total_duration,
        )
        report_path = write_comprehensive_report(run_data, LOG_DIR / "json")
        logger.info(f"JSON report written to: {report_path}")
        print_rich_summary(run_data)

        await _record_and_notify(config, run_data)
        await ping_success(config.healthcheck_url)

    except Exception:
        await ping_failure(config.healthcheck_url, traceback.format_exc())
        raise

    finally:
        await close_validation_client()
        await close_async_client()
        await close_healthcheck_client()
        await Database.close()
        clear_gene_cache()


async def run_pmid_pipeline(
    pmid_file: Path,
    skip_validation: bool = False,
    config: PipelineConfig | None = None,
) -> None:
    """Run LLM extraction on specific PMIDs from a text file (no database).

    Reads PMIDs from a plain text file (one per line, blank lines and
    ``#`` comment lines ignored), fetches fulltext via PubMed/Unpaywall,
    runs LLM extraction + optional NCBI validation, and writes a JSON
    report with a rich console summary.

    Args:
        pmid_file: Path to a text file containing one PMID per line.
        skip_validation: If True, skip NCBI gene validation.
        config: Pipeline configuration (uses defaults if None).

    Raises:
        FileNotFoundError: If pmid_file does not exist.
        ValueError: If the file contains no valid PMIDs.
    """
    if config is None:
        config = PipelineConfig()

    if not pmid_file.exists():
        raise FileNotFoundError(f"PMID file not found: {pmid_file}")

    # Parse PMIDs: skip blank lines and # comments, validate format, dedupe
    raw_lines = pmid_file.read_text().splitlines()
    seen: set[str] = set()
    pmids: list[str] = []
    for line in raw_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            pmid = validate_pmid(stripped)
        except ValueError:
            logger.warning(f"Skipping invalid PMID: {stripped!r}")
            continue
        if pmid not in seen:
            seen.add(pmid)
            pmids.append(pmid)

    if not pmids:
        raise ValueError(f"No valid PMIDs found in {pmid_file}")

    metrics = PipelineMetrics()
    rate_limiter = AsyncRateLimiter(rpm=config.rpm_limit, tpm=config.tpm_limit)

    init_validation_state(config)
    init_ncbi_fetch_state(config)

    pipeline_start_time = time.monotonic()
    await ping_start(config.healthcheck_url)

    logger.info(f"Starting PMID pipeline: {len(pmids)} PMIDs from {pmid_file}")
    model_display = (
        config.ollama_model if config.llm_provider == "ollama" else config.llm_model
    )
    logger.info(
        f"Config: model={model_display}, provider={config.llm_provider}, "
        f"validation={'disabled' if skip_validation else 'enabled'}"
    )

    results: list[PaperResult] = []
    all_genes: list[GeneEntry] = []

    try:
        semaphore = asyncio.Semaphore(config.max_concurrent_papers)

        async def _process_one(idx: int, pmid: str) -> PaperResult:
            async with semaphore:
                logger.info(f"[{idx}/{len(pmids)}] Processing PMID {pmid}")
                start_time = time.monotonic()
                try:
                    # Fetch metadata (DOI) and fulltext
                    metadata = await fetch_paper_metadata(pmid)
                    doi = metadata.get("doi")
                    text_result = await get_fulltext(pmid, doi)

                    text = text_result.get("text")
                    if not text:
                        logger.warning(f"  No text available for PMID {pmid}")
                        return PaperResult(
                            pmid=pmid,
                            error="no text available",
                            processing_time=time.monotonic() - start_time,
                        )

                    is_fulltext = text_result["fulltext"]
                    source = text_result["source"]
                    if is_fulltext:
                        logger.info(f"  Retrieved full text from {source}")
                    else:
                        logger.info("  Using abstract only")

                    # LLM extraction
                    llm_start = time.monotonic()
                    genes, token_usage = await extract_from_paper(
                        text, pmid, config=config, rate_limiter=rate_limiter
                    )
                    llm_elapsed = time.monotonic() - llm_start
                    metrics.genes_extracted += len(genes)
                    metrics.token_usage += token_usage

                    for gene in genes:
                        gene.pmid = pmid

                    logger.info(f"  Extracted {len(genes)} genes")

                    # Validation
                    validation_start = time.monotonic()
                    if skip_validation:
                        # Still apply confidence threshold (cheap, local check)
                        validated_genes = []
                        rejected_genes: list[RejectedGene] = []
                        for gene in genes:
                            if gene.confidence < config.confidence_threshold:
                                rejected_genes.append(
                                    RejectedGene(
                                        gene=gene,
                                        reasons=[
                                            f"Low confidence: {gene.confidence:.2f}"
                                            f" < {config.confidence_threshold}"
                                        ],
                                    )
                                )
                                metrics.genes_rejected += 1
                            else:
                                validated_genes.append(gene)
                                metrics.genes_validated += 1
                    else:
                        validated_genes, rejected_genes = await _validate_genes(
                            genes, metrics, config
                        )
                    validation_elapsed = time.monotonic() - validation_start

                    if is_fulltext:
                        metrics.fulltext_retrieved += 1
                    else:
                        metrics.abstract_only += 1
                    metrics.papers_processed += 1

                    return PaperResult(
                        pmid=pmid,
                        genes=validated_genes,
                        rejected_genes=rejected_genes,
                        fulltext=is_fulltext,
                        source=source,
                        processing_time=time.monotonic() - start_time,
                        llm_time=llm_elapsed,
                        validation_time=validation_elapsed,
                    )
                except Exception as e:
                    logger.exception(f"Error processing PMID {pmid}")
                    return PaperResult(
                        pmid=pmid,
                        error=str(e),
                        processing_time=time.monotonic() - start_time,
                    )

        # Process all PMIDs concurrently
        async with asyncio.TaskGroup() as tg:
            tasks = [
                tg.create_task(_process_one(idx, pmid))
                for idx, pmid in enumerate(pmids, 1)
            ]
        results = [task.result() for task in tasks]

        for result in results:
            if result.succeeded:
                all_genes.extend(result.genes)

        # Batch validation (warning-only)
        batch_warnings: list[str] = []
        if all_genes:
            batch_warnings = batch_validate(all_genes)
            for warning in batch_warnings:
                logger.warning(f"  Batch check: {warning}")

        # Report
        total_duration = time.monotonic() - pipeline_start_time
        run_data = build_pmid_run_data(
            metrics,
            results,
            batch_warnings,
            config,
            pmid_file,
            skip_validation,
            total_duration,
        )
        report_path = write_comprehensive_report(run_data, LOG_DIR / "json")
        logger.info(f"JSON report written to: {report_path}")
        print_rich_summary(run_data)

        await _record_and_notify(config, run_data)
        await ping_success(config.healthcheck_url)

    except Exception:
        await ping_failure(config.healthcheck_url, traceback.format_exc())
        raise

    finally:
        await _close_metadata_client()
        await close_http_client()
        await close_validation_client()
        await close_async_client()
        await close_healthcheck_client()
        await Database.close()
        clear_gene_cache()


async def run_external_data_sync(
    config: PipelineConfig | None = None,
    manage_lifecycle: bool = True,
) -> dict[str, Any]:
    """Sync NCBI Gene / UniProt / PubMed-citation metadata for all genes.

    Clinical trial discovery is a separate pipeline; this function reads
    whatever rows the most recent CT sync wrote to the ``clinical_trials``
    table to build its Table 2 gene list.

    Args:
        config: Pipeline configuration (uses defaults if None).
        manage_lifecycle: When True, handle healthcheck pings internally.
            Set to False by the dispatcher when coalescing multiple pipelines.

    Returns:
        Per-pipeline summary dict suitable for combined notification rendering.
    """
    from pipeline.external_data_sync import sync_all_external_data

    if config is None:
        config = PipelineConfig()

    if manage_lifecycle:
        await ping_start(config.healthcheck_url)
    logger.info("Starting external data sync...")
    try:
        result = await sync_all_external_data(config=config)
        logger.info(LOG_SEPARATOR)
        logger.info("External Data Sync Summary:")
        logger.info(result.summary())
        logger.info(LOG_SEPARATOR)
        if manage_lifecycle:
            await ping_success(config.healthcheck_url)
        return {
            "name": "external_sync",
            "status": "failed" if result.errors else "ok",
            "metrics": {
                "ncbi_fetched": result.ncbi_fetched,
                "ncbi_cached": result.ncbi_cached,
                "ncbi_failed": result.ncbi_failed,
                "uniprot_fetched": result.uniprot_fetched,
                "uniprot_cached": result.uniprot_cached,
                "uniprot_failed": result.uniprot_failed,
                "pubmed_fetched": result.pubmed_fetched,
                "pubmed_cached": result.pubmed_cached,
                "pubmed_failed": result.pubmed_failed,
            },
            "errors": result.errors,
        }
    except Exception:
        if manage_lifecycle:
            await ping_failure(config.healthcheck_url, traceback.format_exc())
        raise
    finally:
        if manage_lifecycle:
            await close_healthcheck_client()
            await Database.close()


async def run_clinical_trials_pipeline(
    config: PipelineConfig | None = None,
    manage_lifecycle: bool = True,
) -> dict[str, Any]:
    """Run the ClinicalTrials.gov discovery pipeline.

    Fetches cSVD-relevant drug trials from the ClinicalTrials.gov v2 API
    and upserts them into the ``clinical_trials`` table. Curator-owned
    columns are preserved; only API-sourced columns are written.

    When ``config.ct_enabled`` is False, the pipeline is a no-op and the
    summary reports ``status="skipped"``.

    Args:
        config: Pipeline configuration (uses defaults if None).
        manage_lifecycle: When True, handle healthcheck pings internally.
            Set to False by the dispatcher when coalescing multiple pipelines.

    Returns:
        Per-pipeline summary dict suitable for combined notification rendering.
    """
    if config is None:
        config = PipelineConfig()

    if manage_lifecycle:
        await ping_start(config.healthcheck_url)

    if not config.ct_enabled:
        logger.warning("ClinicalTrials.gov sync disabled (ct_enabled=False); skipping")
        if manage_lifecycle:
            await ping_success(config.healthcheck_url)
            await close_healthcheck_client()
        return {
            "name": "clinical_trials",
            "status": "skipped",
            "metrics": {"fetched": 0, "cached": 0, "failed": 0},
            "errors": [],
        }

    Database.set_config(config)
    logger.info("Starting ClinicalTrials.gov pipeline...")

    try:
        ctg_result = await sync_clinical_trials(config)
        logger.info(LOG_SEPARATOR)
        logger.info(
            f"ClinicalTrials.gov: {ctg_result.fetched} fetched, "
            f"{ctg_result.cached} upserted, "
            f"{ctg_result.failed} failed"
        )
        logger.info(LOG_SEPARATOR)
        if manage_lifecycle:
            await ping_success(config.healthcheck_url)
        return {
            "name": "clinical_trials",
            "status": "failed" if ctg_result.errors else "ok",
            "metrics": {
                "fetched": ctg_result.fetched,
                "cached": ctg_result.cached,
                "failed": ctg_result.failed,
            },
            "errors": ctg_result.errors,
        }
    except Exception:
        if manage_lifecycle:
            await ping_failure(config.healthcheck_url, traceback.format_exc())
        raise
    finally:
        await close_ctg_client()
        if manage_lifecycle:
            await close_healthcheck_client()
            await Database.close()


def _failure_summary(name: str, error: BaseException) -> dict[str, Any]:
    return {
        "name": name,
        "status": "failed",
        "metrics": {},
        "errors": [str(error)],
    }


async def _run_summary_pipeline(
    coro: Awaitable[dict[str, Any]],
    pipeline_name: str,
    display_label: str,
) -> tuple[dict[str, Any], str | None]:
    """Run a pipeline coroutine that returns a summary dict, catching errors.

    Returns ``(summary, traceback_str)`` where ``traceback_str`` is non-None
    iff the coroutine raised — the caller uses it to surface the first
    failure's trace in the healthcheck ping.
    """
    try:
        return await coro, None
    except Exception as e:
        logger.exception(f"{display_label} failed")
        return _failure_summary(pipeline_name, e), traceback.format_exc()


async def _run_selected_pipelines(
    args: argparse.Namespace,
    config: PipelineConfig,
) -> int:
    """Run the online pipelines selected on the command line, in sequence.

    Owns the single healthcheck ping and the single combined notification
    for the invocation. Continues on per-pipeline failure so that one
    pipeline's error doesn't silently skip the others.

    Returns the process exit code (0 on full success, 1 if any pipeline failed).
    """
    await ping_start(config.healthcheck_url)

    summaries: list[dict[str, Any]] = []
    pubmed_run_data: PipelineRunData | None = None
    first_failure_trace: str | None = None

    def record_failure(trace: str) -> None:
        nonlocal first_failure_trace
        if first_failure_trace is None:
            first_failure_trace = trace

    try:
        if args.pubmed:
            try:
                metrics, pubmed_run_data = await run_pipeline(
                    days_back=args.days_back,
                    dry_run=args.dry_run,
                    test_mode=args.test_mode,
                    config=config,
                    manage_lifecycle=False,
                )
                summaries.append(
                    {
                        "name": "pubmed",
                        "status": "ok",
                        "metrics": {
                            "papers_processed": metrics.papers_processed,
                            "fulltext_retrieved": metrics.fulltext_retrieved,
                            "genes_extracted": metrics.genes_extracted,
                            "genes_validated": metrics.genes_validated,
                            "genes_rejected": metrics.genes_rejected,
                        },
                        "errors": [],
                    }
                )
            except Exception as e:
                logger.exception("PubMed pipeline failed")
                record_failure(traceback.format_exc())
                summaries.append(_failure_summary("pubmed", e))

        if args.clinical_trials:
            summary, trace = await _run_summary_pipeline(
                run_clinical_trials_pipeline(config=config, manage_lifecycle=False),
                "clinical_trials",
                "Clinical trials pipeline",
            )
            summaries.append(summary)
            if trace is not None:
                record_failure(trace)

        if args.sync_external_data:
            summary, trace = await _run_summary_pipeline(
                run_external_data_sync(config=config, manage_lifecycle=False),
                "external_sync",
                "External data sync",
            )
            summaries.append(summary)
            if trace is not None:
                record_failure(trace)

        any_failed = any(s["status"] == "failed" for s in summaries)
        pubmed_only = len(summaries) == 1 and summaries[0]["name"] == "pubmed"

        # PubMed early-exits (no new papers, all already processed, test-mode
        # preview) leave pubmed_run_data=None. Preserve the pre-split behavior
        # of emitting no notification in that specific case.
        if pubmed_only and pubmed_run_data is not None:
            await _record_and_notify(config, pubmed_run_data)
        elif summaries and not (pubmed_only and pubmed_run_data is None):
            combined: dict[str, Any] = {
                "timestamp": datetime.now(tz=UTC).isoformat(),
                "pipelines": summaries,
                "pipeline_config": {
                    "mode": "combined",
                    "model": config.llm_model,
                    "effort": config.llm_effort,
                },
            }
            await _record_and_notify(config, combined)

        if any_failed:
            await ping_failure(
                config.healthcheck_url,
                first_failure_trace or "pipeline failed",
            )
            return 1

        await ping_success(config.healthcheck_url)

    except Exception:
        await ping_failure(config.healthcheck_url, traceback.format_exc())
        raise
    finally:
        await close_async_client()
        await close_healthcheck_client()
        await Database.close()

    return 0


def main() -> None:
    """CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args()

    offline_modes = [args.local_pdfs, args.pmids]
    online_modes = [args.pubmed, args.clinical_trials, args.sync_external_data]

    # Offline modes are mutually exclusive with each other and with online
    # modes. Validate up front.
    if sum(1 for m in offline_modes if m) > 1:
        parser.error("--local-pdfs and --pmids cannot be combined")
    offline_selected = any(offline_modes)
    online_selected = any(online_modes)
    if offline_selected and online_selected:
        parser.error(
            "--local-pdfs / --pmids cannot be combined with --pubmed,"
            " --clinical-trials, or --sync-external-data"
        )

    if args.skip_validation and not offline_selected:
        parser.error("--skip-validation requires --local-pdfs or --pmids")

    # PubMed-scoped flags are harmless for offline modes (they share the
    # --days-back / --test-mode argparse slots) but must not be combined
    # with CT-only or sync-only runs.
    pubmed_only_flags_set = args.test_mode or args.dry_run or args.days_back != 7
    if pubmed_only_flags_set and online_selected and not args.pubmed:
        logger.warning(
            "--days-back / --dry-run / --test-mode are PubMed-only;"
            " ignoring because --pubmed was not selected"
        )

    # Default: if no selection flag was given, run the PubMed pipeline
    # (preserves the original no-flag behavior).
    if not offline_selected and not online_selected:
        args.pubmed = True

    if args.llm_provider is not None:
        os.environ["PIPELINE_LLM_PROVIDER"] = args.llm_provider
    if args.ollama_model is not None:
        os.environ["PIPELINE_OLLAMA_MODEL"] = args.ollama_model

    config = PipelineConfig()

    try:
        if args.local_pdfs:
            asyncio.run(
                run_local_pdf_pipeline(
                    pdf_dir=args.local_pdfs,
                    skip_validation=args.skip_validation,
                    config=config,
                )
            )
        elif args.pmids:
            asyncio.run(
                run_pmid_pipeline(
                    pmid_file=args.pmids,
                    skip_validation=args.skip_validation,
                    config=config,
                )
            )
        else:
            exit_code = asyncio.run(_run_selected_pipelines(args, config))
            if exit_code != 0:
                sys.exit(exit_code)
    except (ValueError, FileNotFoundError) as e:
        logger.error(f"Invalid argument: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Pipeline interrupted by user")
        sys.exit(130)


if __name__ == "__main__":
    main()
