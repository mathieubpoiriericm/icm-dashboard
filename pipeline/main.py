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
notification per invocation.

Usage:
    python pipeline/main.py [--days-back N] [--dry-run] [--test-mode]
    python pipeline/main.py --clinical-trials
    python pipeline/main.py --pubmed --clinical-trials
    python pipeline/main.py --sync-external-data
    python pipeline/main.py --local-pdfs PATH [--skip-validation]
    python pipeline/main.py --pmids FILE [--skip-validation]
"""

import argparse
import sys
from pathlib import Path

DEFAULT_DAYS_BACK = 7


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser (stdlib-only, no heavy imports)."""
    parser = argparse.ArgumentParser(description="SVD Dashboard data pipeline")
    parser.add_argument(
        "--days-back",
        type=int,
        default=DEFAULT_DAYS_BACK,
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
import contextlib
import json
import logging
import time
import traceback
from collections.abc import Awaitable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final, Literal, TypedDict

ProgressStatus = Literal["running", "completed", "error"]

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
import signal

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
from pipeline.data_merger import MergeResult, merge_gene_entries
from pipeline.database import (
    Database,
    get_existing_pmids,
    record_pipeline_run,
    record_processed_pmids_batch,
    reset_gene_sequence,
)
from pipeline.event_log import EventLog
from pipeline.http_client import AsyncHttpClientManager
from pipeline.llm_extraction import (
    ExtractionFailedError,
    close_async_client,
    extract_from_paper,
)
from pipeline.llm_providers.base import GeneEntry
from pipeline.notifications import send_pipeline_notification
from pipeline.pdf_retrieval import (
    close_http_client,
    get_fulltext,
    parse_local_pdf,
)
from pipeline.pubmed_search import filter_new_pmids, search_recent_papers
from pipeline.quality_metrics import PipelineMetrics
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
_STAGES: Final[tuple[str, ...]] = (
    "searching_pubmed",
    "filtering_pmids",
    "processing_papers",
    "batch_validation",
    "merging_database",
    "finalizing",
)
_TOTAL_STAGES: Final[int] = len(_STAGES)


def _write_progress(
    config: PipelineConfig,
    *,
    status: ProgressStatus,
    stage: str,
    stage_number: int,
    error_message: str | None = None,
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
        "stage_number": stage_number,
        "total_stages": _TOTAL_STAGES,
        "updated_at": datetime.now(tz=UTC).isoformat(),
        "error_message": error_message,
    }
    try:
        tmp_path.write_text(json.dumps(data) + "\n")
        tmp_path.replace(progress_path)
    except OSError:
        logger.debug("Failed to write progress file %s", progress_path, exc_info=True)


@dataclass(slots=True)
class _ProgressReporter:
    """Track and persist the current PubMed pipeline stage."""

    config: PipelineConfig
    stage_index: int = 0
    finalized: bool = False

    def __post_init__(self) -> None:
        Path(self.config.progress_file).parent.mkdir(parents=True, exist_ok=True)

    def report(self, stage_index: int) -> None:
        """Advance to and persist a running stage."""
        self.stage_index = stage_index
        _write_progress(
            self.config,
            status="running",
            stage=_STAGES[stage_index],
            stage_number=stage_index + 1,
        )

    def finalize(
        self,
        *,
        status: ProgressStatus,
        stage_number: int | None = None,
        error_message: str | None = None,
    ) -> None:
        """Persist a terminal progress state."""
        _write_progress(
            self.config,
            status=status,
            stage=_STAGES[self.stage_index],
            stage_number=(
                stage_number if stage_number is not None else self.stage_index + 1
            ),
            error_message=error_message,
        )
        self.finalized = True

    def fail(self, exc: BaseException) -> None:
        """Persist an interrupted or failed terminal state."""
        if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)):
            error_message = f"Run was interrupted ({type(exc).__name__})"
        else:
            error_message = traceback.format_exc()[:500]
        self.finalize(
            status="error",
            error_message=error_message,
        )

    def ensure_terminal_state(self) -> None:
        """Write a defensive failure state if no explicit final state exists."""
        if not self.finalized:
            self.finalize(
                status="error",
                error_message="Pipeline exited without finalizing progress",
            )


# --- Type definitions ---
class MetadataResult(TypedDict):
    """Result from metadata fetch."""

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
    processing_time: float = 0.0
    pdf_parse_time: float = 0.0
    llm_time: float = 0.0
    validation_time: float = 0.0

    @property
    def succeeded(self) -> bool:
        return self.error is None


@dataclass(slots=True)
class ExtractionOutcome:
    """Validated extraction data and timings shared by all input modes."""

    genes: list[GeneEntry]
    rejected_genes: list[RejectedGene]
    extracted_count: int
    llm_time: float
    validation_time: float


async def _validate_genes(
    genes: list[GeneEntry],
    metrics: PipelineMetrics,
    config: PipelineConfig,
) -> tuple[list[GeneEntry], list[RejectedGene]]:
    """Validate genes concurrently against NCBI and return (valid, rejected) lists."""
    validated_genes: list[GeneEntry] = []
    rejected_genes: list[RejectedGene] = []
    results = await asyncio.gather(
        *(validate_gene_entry(gene, config=config) for gene in genes),
        return_exceptions=True,
    )

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


def _filter_genes_by_confidence(
    genes: list[GeneEntry],
    metrics: PipelineMetrics,
    threshold: float,
) -> tuple[list[GeneEntry], list[RejectedGene]]:
    """Apply the local confidence check used when NCBI validation is skipped."""
    validated: list[GeneEntry] = []
    rejected: list[RejectedGene] = []
    for gene in genes:
        if gene.confidence < threshold:
            rejected.append(
                RejectedGene(
                    gene=gene,
                    reasons=[f"Low confidence: {gene.confidence:.2f} < {threshold}"],
                )
            )
            metrics.genes_rejected += 1
        else:
            validated.append(gene)
            metrics.genes_validated += 1
    return validated, rejected


async def _extract_and_validate(
    text: str,
    paper_id: str,
    metrics: PipelineMetrics,
    config: PipelineConfig,
    rate_limiter: AsyncRateLimiter | None,
    *,
    skip_validation: bool = False,
) -> ExtractionOutcome:
    """Run the LLM and the validation policy shared by all pipeline modes."""
    llm_start = time.monotonic()
    try:
        genes, token_usage = await extract_from_paper(
            text,
            paper_id,
            config=config,
            rate_limiter=rate_limiter,
        )
    except ExtractionFailedError as exc:
        if exc.token_usage is not None:
            metrics.token_usage += exc.token_usage
        raise
    llm_time = time.monotonic() - llm_start

    metrics.genes_extracted += len(genes)
    metrics.token_usage += token_usage
    for gene in genes:
        gene.pmid = paper_id

    validation_start = time.monotonic()
    if skip_validation:
        validated, rejected = _filter_genes_by_confidence(
            genes, metrics, config.confidence_threshold
        )
    else:
        validated, rejected = await _validate_genes(genes, metrics, config)

    return ExtractionOutcome(
        genes=validated,
        rejected_genes=rejected,
        extracted_count=len(genes),
        llm_time=llm_time,
        validation_time=time.monotonic() - validation_start,
    )


def _collect_successful_genes(results: list[PaperResult]) -> list[GeneEntry]:
    """Flatten genes from successful paper results."""
    return [gene for result in results if result.succeeded for gene in result.genes]


def _run_batch_validation(genes: list[GeneEntry]) -> list[str]:
    """Run warning-only batch checks and emit each warning consistently."""
    warnings = batch_validate(genes) if genes else []
    for warning in warnings:
        logger.warning(f"  Batch check: {warning}")
    return warnings


def _resolve_pdf_files(path: Path) -> tuple[Path, list[Path]]:
    """Resolve one PDF or a directory into its parent and sorted PDF files."""
    if path.is_file():
        if path.suffix.lower() != ".pdf":
            raise ValueError(f"Not a PDF file: {path}")
        return path.parent, [path]
    if not path.is_dir():
        raise FileNotFoundError(f"Path not found: {path}")

    pdf_files = sorted(path.glob("*.pdf"))
    if not pdf_files:
        raise ValueError(f"No .pdf files found in {path}")
    return path, pdf_files


def _load_pmids(path: Path) -> list[str]:
    """Load, validate, and order-deduplicate PMIDs from a text file."""
    if not path.exists():
        raise FileNotFoundError(f"PMID file not found: {path}")

    pmids: list[str] = []
    for line in path.read_text().splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        try:
            pmids.append(validate_pmid(value))
        except ValueError:
            logger.warning(f"Skipping invalid PMID: {value!r}")

    if not (unique_pmids := list(dict.fromkeys(pmids))):
        raise ValueError(f"No valid PMIDs found in {path}")
    return unique_pmids


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
        MetadataResult with the paper DOI, when available.
    """
    pmid = validate_pmid(pmid)

    params: dict[str, str] = {"db": "pubmed", "id": pmid, "retmode": "xml"}

    if api_key := os.getenv("NCBI_API_KEY"):
        params["api_key"] = api_key

    try:
        client = await _get_metadata_client()
        resp = await client.get(NCBI_EFETCH_URL, params=params)

        if resp.status_code != 200:
            logger.warning(f"Metadata fetch failed for PMID {pmid}: {resp.status_code}")
        else:
            root = etree.fromstring(resp.content, parser=SAFE_XML_PARSER)
            doi_elem = root.find(".//ArticleId[@IdType='doi']")
            doi_text = doi_elem.text if doi_elem is not None else None
            doi = doi_text if isinstance(doi_text, str) else None
            return {"doi": doi}

    except httpx.TimeoutException:
        logger.warning(f"Timeout fetching metadata for PMID {pmid}")
    except httpx.RequestError as e:
        logger.warning(f"Request error fetching metadata for PMID {pmid}: {e}")
    except etree.XMLSyntaxError as e:
        logger.error(f"XML parsing failed for PMID {pmid}: {e}")

    return {"doi": None}


async def _record_and_notify(config: PipelineConfig, run_data: Any) -> None:
    """Record pipeline run to event log and send a notification.

    Offloads the blocking SQLite + Apprise work to a worker thread so the
    asyncio event loop isn't stalled during the final flush.
    """

    def _run() -> None:
        with EventLog(config.event_db_path) as event_log:
            event_log.record("pipeline_completed", run_data)
            send_pipeline_notification(run_data, config)

    await asyncio.to_thread(_run)


async def _finalize_run(
    metrics: PipelineMetrics,
    run_data: PipelineRunData,
    run_mode: str,
) -> None:
    """Record run stats to database.

    Notifications are handled separately by the caller (see ``main()``) so
    they can be coalesced across multiple pipelines in one invocation.
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

    outcome = await _extract_and_validate(
        text,
        pmid,
        metrics,
        config,
        rate_limiter,
    )
    logger.info(f"  Extracted {outcome.extracted_count} genes")

    return {
        "genes": outcome.genes,
        "rejected_genes": outcome.rejected_genes,
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
            return PaperResult(
                pmid=pmid,
                genes=result["genes"],
                rejected_genes=result["rejected_genes"],
                fulltext=result["fulltext"],
                source=result["source"],
                processing_time=time.monotonic() - start_time,
            )
        except Exception as e:
            logger.exception(f"Error processing PMID {pmid}")
            return PaperResult(
                pmid=pmid,
                error=str(e),
                processing_time=time.monotonic() - start_time,
            )


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


@dataclass(slots=True)
class _ProcessedBatch:
    """Paper-processing outputs needed by reporting and database merge stages."""

    results: list[PaperResult]
    successful_results: list[PaperResult]
    genes: list[GeneEntry]
    warnings: list[str]


async def _discover_new_pmids(
    days_back: int,
    *,
    dry_run: bool,
    test_mode: bool,
    progress: _ProgressReporter,
) -> tuple[list[str], list[str]]:
    """Search PubMed and filter previously processed identifiers."""
    progress.report(0)
    print("##STAGE:search##", flush=True)
    logger.info("Step 1: Searching PubMed for recent SVD genetic papers...")
    all_pmids = await search_recent_papers(days_back)
    logger.info(f"  Found {len(all_pmids)} papers matching SVD genetic criteria")
    if not all_pmids:
        return [], []

    progress.report(1)
    print("##STAGE:retrieve##", flush=True)
    logger.info("Step 2: Filtering already-processed papers...")
    if dry_run or test_mode:
        existing_pmids: set[str] = set()
        logger.info("  Skipping PMID deduplication (dry-run/test mode)")
    else:
        try:
            existing_pmids = await get_existing_pmids()
        except asyncpg.UndefinedTableError:
            # A missing table is expected only on the first run. Other
            # database failures must propagate to avoid silently reprocessing.
            logger.warning(
                "  pubmed_refs table missing; treating as empty (first run?)"
            )
            existing_pmids = set()

    new_pmids = filter_new_pmids(all_pmids, existing_pmids)
    logger.info(f"  {len(new_pmids)} new papers to process")
    return all_pmids, new_pmids


def _log_test_preview(pmids: list[str], config: PipelineConfig) -> None:
    """Log the bounded PMID preview used by test mode."""
    logger.info("Test mode enabled - skipping LLM extraction and database merge")
    logger.info(f"  Would process {len(pmids)} papers:")
    for pmid in pmids[: config.test_mode_preview_count]:
        logger.info(f"    PMID: {pmid}")
    if len(pmids) > config.test_mode_preview_count:
        remaining = len(pmids) - config.test_mode_preview_count
        logger.info(f"    ... and {remaining} more")


async def _process_new_pmids(
    pmids: list[str],
    metrics: PipelineMetrics,
    config: PipelineConfig,
    rate_limiter: AsyncRateLimiter,
    progress: _ProgressReporter,
) -> _ProcessedBatch:
    """Process, flatten, and batch-validate a set of new papers."""
    progress.report(2)
    print("##STAGE:extract##", flush=True)
    logger.info("Step 3: Processing papers concurrently...")
    results = await process_papers_concurrently(
        pmids, metrics, config=config, rate_limiter=rate_limiter
    )
    successful_results = [result for result in results if result.succeeded]
    genes = _collect_successful_genes(results)
    metrics.papers_processed += len(successful_results)
    logger.info(f"  Processed {metrics.papers_processed} papers")
    logger.info(f"  Validated: {metrics.genes_validated} genes")

    progress.report(3)
    print("##STAGE:validate##", flush=True)
    return _ProcessedBatch(
        results=results,
        successful_results=successful_results,
        genes=genes,
        warnings=_run_batch_validation(genes),
    )


async def _merge_processed_batch(
    batch: _ProcessedBatch,
    progress: _ProgressReporter,
) -> MergeResult | None:
    """Merge accepted genes and record successfully processed PMIDs."""
    progress.report(4)
    print("##STAGE:merge##", flush=True)
    logger.info("Step 4: Merging validated data into database...")
    await reset_gene_sequence()

    gene_result = await merge_gene_entries(batch.genes) if batch.genes else None
    if gene_result is not None:
        logger.info(
            f"  Genes: {gene_result['inserted']} inserted, "
            f"{gene_result['updated']} updated"
        )

    pmid_records = [
        (result.pmid, result.fulltext, result.source, len(result.genes))
        for result in batch.successful_results
    ]
    recorded = await record_processed_pmids_batch(pmid_records)
    logger.info(f"  Recorded {recorded} processed PMIDs")
    progress.report(5)
    print("##STAGE:sync##", flush=True)
    return gene_result


async def _complete_pubmed_run(
    metrics: PipelineMetrics,
    run_data: PipelineRunData,
    config: PipelineConfig,
    progress: _ProgressReporter,
    *,
    dry_run: bool,
    manage_lifecycle: bool,
) -> None:
    """Persist live-run stats, notify, and finalize live-run progress."""
    if not dry_run:
        await _finalize_run(metrics, run_data, "standard")
    if manage_lifecycle:
        await _record_and_notify(config, run_data)
    if not dry_run:
        progress.finalize(
            status="completed",
            stage_number=_TOTAL_STAGES,
        )


def _emit_report(run_data: PipelineRunData) -> None:
    """Write and print a pipeline report consistently across run modes."""
    report_path = write_comprehensive_report(run_data, LOG_DIR / "json")
    logger.info(f"JSON report written to: {report_path}")
    print_rich_summary(run_data)


def _build_pubmed_report(
    metrics: PipelineMetrics,
    batch: _ProcessedBatch,
    gene_result: MergeResult | None,
    config: PipelineConfig,
    *,
    days_back: int,
    dry_run: bool,
    total_pmids_found: int,
    started_at: float,
) -> PipelineRunData:
    """Build, persist, and print a standard PubMed run report."""
    run_data = build_run_data(
        metrics=metrics,
        results=batch.results,
        gene_result=gene_result,
        batch_warnings=batch.warnings,
        config=config,
        days_back=days_back,
        dry_run=dry_run,
        total_pmids_found=total_pmids_found,
        new_pmids_count=len(batch.results),
        total_duration=time.monotonic() - started_at,
    )
    _emit_report(run_data)
    return run_data


async def _process_local_pdf_file(
    pdf_path: Path,
    metrics: PipelineMetrics,
    config: PipelineConfig,
    rate_limiter: AsyncRateLimiter,
    *,
    skip_validation: bool,
) -> PaperResult:
    """Parse and process one local PDF with per-step timings."""
    file_id = pdf_path.stem
    started_at = time.monotonic()
    try:
        parse_started_at = time.monotonic()
        text = await asyncio.to_thread(parse_local_pdf, pdf_path)
        parse_time = time.monotonic() - parse_started_at
        if not text:
            logger.warning(f"  No text extracted from {pdf_path.name}")
            return PaperResult(
                pmid=file_id,
                error="empty or corrupt PDF",
                processing_time=time.monotonic() - started_at,
                pdf_parse_time=parse_time,
            )

        outcome = await _extract_and_validate(
            text,
            file_id,
            metrics,
            config,
            rate_limiter,
            skip_validation=skip_validation,
        )
        logger.info(f"  Extracted {outcome.extracted_count} genes from {pdf_path.name}")
        metrics.papers_processed += 1
        metrics.fulltext_retrieved += 1
        return PaperResult(
            pmid=file_id,
            genes=outcome.genes,
            rejected_genes=outcome.rejected_genes,
            fulltext=True,
            source="local_pdf",
            processing_time=time.monotonic() - started_at,
            pdf_parse_time=parse_time,
            llm_time=outcome.llm_time,
            validation_time=outcome.validation_time,
        )
    except Exception as exc:
        logger.exception(f"Error processing {pdf_path.name}")
        return PaperResult(
            pmid=file_id,
            error=str(exc),
            processing_time=time.monotonic() - started_at,
        )


async def _process_pmid_item(
    pmid: str,
    metrics: PipelineMetrics,
    config: PipelineConfig,
    rate_limiter: AsyncRateLimiter,
    *,
    skip_validation: bool,
) -> PaperResult:
    """Fetch and process one PMID for the offline PMID-list mode."""
    started_at = time.monotonic()
    try:
        metadata = await fetch_paper_metadata(pmid)
        text_result = await get_fulltext(pmid, metadata.get("doi"))
        text = text_result.get("text")
        if not text:
            logger.warning(f"  No text available for PMID {pmid}")
            return PaperResult(
                pmid=pmid,
                error="no text available",
                processing_time=time.monotonic() - started_at,
            )

        is_fulltext = text_result["fulltext"]
        source = text_result["source"]
        logger.info(
            f"  Retrieved full text from {source}"
            if is_fulltext
            else "  Using abstract only"
        )
        outcome = await _extract_and_validate(
            text,
            pmid,
            metrics,
            config,
            rate_limiter,
            skip_validation=skip_validation,
        )
        logger.info(f"  Extracted {outcome.extracted_count} genes")

        if is_fulltext:
            metrics.fulltext_retrieved += 1
        else:
            metrics.abstract_only += 1
        metrics.papers_processed += 1
        return PaperResult(
            pmid=pmid,
            genes=outcome.genes,
            rejected_genes=outcome.rejected_genes,
            fulltext=is_fulltext,
            source=source,
            processing_time=time.monotonic() - started_at,
            llm_time=outcome.llm_time,
            validation_time=outcome.validation_time,
        )
    except Exception as exc:
        logger.exception(f"Error processing PMID {pmid}")
        return PaperResult(
            pmid=pmid,
            error=str(exc),
            processing_time=time.monotonic() - started_at,
        )


def _install_termination_handlers(
    task: asyncio.Task[Any],
) -> tuple[asyncio.AbstractEventLoop, list[int]]:
    """Install supported SIGTERM/SIGHUP handlers that cancel *task*."""
    loop = asyncio.get_running_loop()
    signals = [signal.SIGTERM]
    if hasattr(signal, "SIGHUP"):
        signals.append(signal.SIGHUP)

    def _cancel(sig: int) -> None:
        logger.warning("Received signal %s; cancelling pipeline run", sig)
        task.cancel()

    installed: list[int] = []
    for sig in signals:
        try:
            loop.add_signal_handler(sig, _cancel, sig)
        except NotImplementedError, RuntimeError, ValueError:
            continue
        installed.append(sig)
    return loop, installed


def _remove_signal_handlers(
    loop: asyncio.AbstractEventLoop, signals: list[int]
) -> None:
    """Remove any termination handlers installed for this run."""
    for sig in signals:
        with contextlib.suppress(NotImplementedError, ValueError):
            loop.remove_signal_handler(sig)


async def run_pipeline(
    days_back: int = DEFAULT_DAYS_BACK,
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
            this function sends its own completion notification. When False
            (set by the ``main()`` dispatcher for combined runs), notifications
            are skipped so the dispatcher can coalesce them across pipelines.

    Returns:
        A tuple of (PipelineMetrics, run_data). ``run_data`` is ``None`` on
        early exits where no pipeline summary is built (no papers found, all
        PMIDs already processed, test-mode preview).

    Raises:
        ValueError: If days_back is out of valid range.
    """
    config = config or PipelineConfig()

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
    # Set up rate limiter
    rate_limiter = AsyncRateLimiter(rpm=config.rpm_limit, tpm=config.tpm_limit)

    pipeline_start_time = time.monotonic()

    logger.info(f"Starting SVD Dashboard pipeline (looking back {days_back} days)")
    logger.info(
        f"Config: model={config.llm_model}, "
        f"concurrency={config.max_concurrent_papers}, "
        f"RPM={config.rpm_limit}, TPM={config.tpm_limit}"
    )

    progress = _ProgressReporter(config)

    # Convert SIGTERM/SIGHUP into task cancellation so the except/finally
    # blocks below can write a terminal progress state. SIGINT is left to
    # asyncio's default handling (which raises KeyboardInterrupt).
    current_task = asyncio.current_task()
    assert current_task is not None  # always set inside `async def`
    loop, installed_signals = _install_termination_handlers(current_task)

    try:
        all_pmids, new_pmids = await _discover_new_pmids(
            days_back,
            dry_run=dry_run,
            test_mode=test_mode,
            progress=progress,
        )
        if not all_pmids:
            logger.info("No new papers found. Pipeline complete.")
            progress.finalize(
                status="completed",
            )
            return metrics, None

        if not new_pmids:
            logger.info("All papers already processed. Pipeline complete.")
            progress.finalize(
                status="completed",
            )
            return metrics, None

        # Test mode: skip LLM extraction and database merge
        if test_mode:
            _log_test_preview(new_pmids, config)
            progress.finalize(
                status="completed",
            )
            return metrics, None

        batch = await _process_new_pmids(
            new_pmids,
            metrics,
            config,
            rate_limiter,
            progress,
        )
        if dry_run:
            logger.info("Dry run mode - skipping database merge")
            progress.finalize(
                status="completed",
            )
            gene_result = None
        else:
            gene_result = await _merge_processed_batch(batch, progress)

        run_data = _build_pubmed_report(
            metrics,
            batch,
            gene_result,
            config,
            days_back=days_back,
            dry_run=dry_run,
            total_pmids_found=len(all_pmids),
            started_at=pipeline_start_time,
        )

        await _complete_pubmed_run(
            metrics,
            run_data,
            config,
            progress,
            dry_run=dry_run,
            manage_lifecycle=manage_lifecycle,
        )

        return metrics, run_data

    except BaseException as exc:
        # Include cancellation and Ctrl+C so they also write a terminal state.
        progress.fail(exc)
        raise

    finally:
        progress.ensure_terminal_state()
        _remove_signal_handlers(loop, installed_signals)

        # Cleanup shared resources used only by this pipeline. The DB pool
        # is kept open for subsequent pipelines when the dispatcher is
        # managing the lifecycle — it closes the pool itself after all
        # selected pipelines have run.
        await _close_metadata_client()
        await close_http_client()
        await close_validation_client()
        await close_async_client()
        if manage_lifecycle:
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
    config = config or PipelineConfig()

    pdf_dir, pdf_files = _resolve_pdf_files(pdf_dir)

    metrics = PipelineMetrics()
    rate_limiter = AsyncRateLimiter(rpm=config.rpm_limit, tpm=config.tpm_limit)

    init_validation_state(config)
    pipeline_start_time = time.monotonic()

    logger.info(f"Starting local PDF pipeline: {len(pdf_files)} files in {pdf_dir}")
    logger.info(
        f"Config: model={config.llm_model}, "
        f"validation={'disabled' if skip_validation else 'enabled'}"
    )

    try:
        semaphore = asyncio.Semaphore(config.max_concurrent_papers)

        async def _process_pdf(index: int, pdf_path: Path) -> PaperResult:
            async with semaphore:
                logger.info(f"[{index}/{len(pdf_files)}] Processing {pdf_path.name}")
                return await _process_local_pdf_file(
                    pdf_path,
                    metrics,
                    config,
                    rate_limiter,
                    skip_validation=skip_validation,
                )

        async with asyncio.TaskGroup() as tg:
            tasks = [
                tg.create_task(_process_pdf(index, pdf_path))
                for index, pdf_path in enumerate(pdf_files, 1)
            ]

        results = [task.result() for task in tasks]

        all_genes = _collect_successful_genes(results)
        batch_warnings = _run_batch_validation(all_genes)

        # Report
        total_duration = time.monotonic() - pipeline_start_time
        run_data = build_local_pdf_run_data(
            metrics=metrics,
            results=results,
            batch_warnings=batch_warnings,
            config=config,
            pdf_dir=pdf_dir,
            skip_validation=skip_validation,
            total_duration=total_duration,
        )
        _emit_report(run_data)

        await _record_and_notify(config, run_data)

    finally:
        await close_validation_client()
        await close_async_client()
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
    config = config or PipelineConfig()

    pmids = _load_pmids(pmid_file)

    metrics = PipelineMetrics()
    rate_limiter = AsyncRateLimiter(rpm=config.rpm_limit, tpm=config.tpm_limit)

    init_validation_state(config)
    pipeline_start_time = time.monotonic()

    logger.info(f"Starting PMID pipeline: {len(pmids)} PMIDs from {pmid_file}")
    logger.info(
        f"Config: model={config.llm_model}, "
        f"validation={'disabled' if skip_validation else 'enabled'}"
    )

    try:
        semaphore = asyncio.Semaphore(config.max_concurrent_papers)

        async def _process_one(idx: int, pmid: str) -> PaperResult:
            async with semaphore:
                logger.info(f"[{idx}/{len(pmids)}] Processing PMID {pmid}")
                return await _process_pmid_item(
                    pmid,
                    metrics,
                    config,
                    rate_limiter,
                    skip_validation=skip_validation,
                )

        # Process all PMIDs concurrently
        async with asyncio.TaskGroup() as tg:
            tasks = [
                tg.create_task(_process_one(idx, pmid))
                for idx, pmid in enumerate(pmids, 1)
            ]
        results = [task.result() for task in tasks]

        all_genes = _collect_successful_genes(results)
        batch_warnings = _run_batch_validation(all_genes)

        # Report
        total_duration = time.monotonic() - pipeline_start_time
        run_data = build_pmid_run_data(
            metrics=metrics,
            results=results,
            batch_warnings=batch_warnings,
            config=config,
            pmid_file=pmid_file,
            skip_validation=skip_validation,
            total_duration=total_duration,
        )
        _emit_report(run_data)

        await _record_and_notify(config, run_data)

    finally:
        await _close_metadata_client()
        await close_http_client()
        await close_validation_client()
        await close_async_client()
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
        manage_lifecycle: When True, close the database pool on exit.
            Set to False by the dispatcher when coalescing multiple pipelines.

    Returns:
        Per-pipeline summary dict suitable for combined notification rendering.
    """
    from pipeline.external_data_sync import sync_all_external_data

    config = config or PipelineConfig()

    logger.info("Starting external data sync...")
    try:
        result = await sync_all_external_data(config=config)
        logger.info(LOG_SEPARATOR)
        logger.info("External Data Sync Summary:")
        logger.info(result.summary())
        logger.info(LOG_SEPARATOR)
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
    finally:
        if manage_lifecycle:
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
        manage_lifecycle: When True, close the database pool on exit.
            Set to False by the dispatcher when coalescing multiple pipelines.

    Returns:
        Per-pipeline summary dict suitable for combined notification rendering.
    """
    config = config or PipelineConfig()

    if not config.ct_enabled:
        logger.warning("ClinicalTrials.gov sync disabled (ct_enabled=False); skipping")
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
    finally:
        await close_ctg_client()
        if manage_lifecycle:
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
) -> dict[str, Any]:
    """Run a pipeline coroutine that returns a summary dict, catching errors."""
    try:
        return await coro
    except Exception as e:
        logger.exception(f"{display_label} failed")
        return _failure_summary(pipeline_name, e)


async def _run_selected_pipelines(
    args: argparse.Namespace,
    config: PipelineConfig,
) -> int:
    """Run the online pipelines selected on the command line, in sequence.

    Owns the single combined notification for the invocation. Continues on
    per-pipeline failure so that one pipeline's error doesn't silently skip
    the others.

    Returns the process exit code (0 on full success, 1 if any pipeline failed).
    """
    summaries: list[dict[str, Any]] = []
    pubmed_run_data: PipelineRunData | None = None

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
                summaries.append(_failure_summary("pubmed", e))

        if args.clinical_trials:
            summaries.append(
                await _run_summary_pipeline(
                    run_clinical_trials_pipeline(config=config, manage_lifecycle=False),
                    "clinical_trials",
                    "Clinical trials pipeline",
                )
            )

        if args.sync_external_data:
            summaries.append(
                await _run_summary_pipeline(
                    run_external_data_sync(config=config, manage_lifecycle=False),
                    "external_sync",
                    "External data sync",
                )
            )

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

        return int(any_failed)

    finally:
        await close_async_client()
        await Database.close()


def _prepare_cli_args(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> argparse.Namespace:
    """Validate mode combinations and apply the default pipeline selection."""
    offline_modes = (args.local_pdfs, args.pmids)
    online_modes = (args.pubmed, args.clinical_trials, args.sync_external_data)

    if sum(map(bool, offline_modes)) > 1:
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

    pubmed_options_set = (
        args.test_mode or args.dry_run or args.days_back != DEFAULT_DAYS_BACK
    )
    if pubmed_options_set and online_selected and not args.pubmed:
        logger.warning(
            "--days-back / --dry-run / --test-mode are PubMed-only;"
            " ignoring because --pubmed was not selected"
        )

    if not offline_selected and not online_selected:
        args.pubmed = True
    return args


def main() -> None:
    """CLI entry point."""
    parser = _build_parser()
    args = _prepare_cli_args(parser, parser.parse_args())

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
            if exit_code:
                sys.exit(exit_code)
    except (ValueError, FileNotFoundError) as e:
        logger.error(f"Invalid argument: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Pipeline interrupted by user")
        sys.exit(130)


if __name__ == "__main__":
    main()
