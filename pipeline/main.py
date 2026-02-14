#!/Library/Frameworks/Python.framework/Versions/3.14/bin/python3
# PYTHON_ARGCOMPLETE_OK
"""
Main entry point for the SVD Dashboard data pipeline.

This script orchestrates:
1. PubMed search for new SVD-related genetic papers
2. Full-text/abstract retrieval
3. LLM-based gene data extraction using Claude
4. Validation against external databases
5. Batch quality checks (Pandera)
6. Merging validated data into PostgreSQL

Usage:
    python pipeline/main.py [--days-back N] [--dry-run] [--test-mode]
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
        "--sync-external-data",
        action="store_true",
        help="Sync external data (NCBI, UniProt, PubMed) for all genes in database",
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
    import argcomplete

    _parser = _build_parser()
    argcomplete.autocomplete(_parser)
    del _parser
# --- End fast path ---

import asyncio  # noqa: E402
import logging  # noqa: E402
import re  # noqa: E402
import time  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from datetime import datetime  # noqa: E402
from typing import Final, TypedDict  # noqa: E402

import httpx  # noqa: E402
from lxml import etree  # type: ignore[import-untyped]  # noqa: E402

# Add project root to path for imports when running as script
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load environment variables from .env file
from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

import os  # noqa: E402

from pipeline.batch_validation import batch_validate  # noqa: E402
from pipeline.config import PipelineConfig  # noqa: E402
from pipeline.data_merger import merge_gene_entries  # noqa: E402
from pipeline.database import (  # noqa: E402
    Database,
    get_existing_pmids,
    record_processed_pmids_batch,
    reset_sequence,
)
from pipeline.llm_extraction import GeneEntry, extract_from_paper  # noqa: E402
from pipeline.notifications import send_pipeline_summary_email  # noqa: E402
from pipeline.pdf_retrieval import (  # noqa: E402
    close_http_client,
    get_fulltext,
    parse_local_pdf,
)
from pipeline.pubmed_search import filter_new_pmids, search_recent_papers  # noqa: E402
from pipeline.quality_metrics import PipelineMetrics, TokenUsage  # noqa: E402
from pipeline.rate_limiter import AsyncRateLimiter  # noqa: E402
from pipeline.report import (  # noqa: E402
    build_local_pdf_run_data,
    build_pmid_run_data,
    build_run_data,
    print_rich_summary,
    write_comprehensive_report,
)
from pipeline.validation import (  # noqa: E402
    clear_gene_cache,
    close_validation_client,
    validate_gene_entry,
)

# --- Constants ---
LOG_SEPARATOR: Final[str] = "=" * 50
PMID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^\d{1,8}$")

# Configure logging
LOG_DIR = Path(os.getenv("PIPELINE_LOG_DIR", PROJECT_ROOT / "logs"))
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / f"pipeline_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.log"

from rich.logging import RichHandler  # noqa: E402

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


# --- Type definitions ---
class MetadataResult(TypedDict):
    """Result from metadata fetch."""

    pmid: str
    doi: str | None


class PaperProcessResult(TypedDict):
    """Result from processing a single paper."""

    genes: list[GeneEntry]
    fulltext: bool
    source: str


@dataclass(slots=True)
class PaperResult:
    """Result from processing a single paper with error handling."""

    pmid: str
    genes: list[GeneEntry] = field(default_factory=list)
    fulltext: bool = False
    source: str = "none"
    error: str | None = None
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    processing_time: float = 0.0

    @property
    def succeeded(self) -> bool:
        return self.error is None


# --- Shared HTTP client for metadata ---
_metadata_client: httpx.AsyncClient | None = None


async def _get_metadata_client() -> httpx.AsyncClient:
    """Get or create shared HTTP client for metadata fetching."""
    global _metadata_client
    if _metadata_client is None:
        _metadata_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _metadata_client


async def _close_metadata_client() -> None:
    """Close shared metadata HTTP client."""
    global _metadata_client
    if _metadata_client is not None:
        await _metadata_client.aclose()
        _metadata_client = None


def _validate_pmid(pmid: str) -> str:
    """Validate and normalize a PubMed ID."""
    pmid = pmid.strip()
    if not PMID_PATTERN.match(pmid):
        raise ValueError(f"Invalid PMID format: {pmid!r}")
    return pmid


async def fetch_paper_metadata(pmid: str) -> MetadataResult:
    """Fetch DOI and other metadata for a PMID using NCBI efetch.

    Args:
        pmid: PubMed ID.

    Returns:
        MetadataResult with pmid and doi.
    """
    pmid = _validate_pmid(pmid)

    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    params: dict[str, str] = {"db": "pubmed", "id": pmid, "retmode": "xml"}

    if api_key := os.getenv("NCBI_API_KEY"):
        params["api_key"] = api_key

    try:
        client = await _get_metadata_client()
        resp = await client.get(url, params=params)

        if resp.status_code != 200:
            logger.warning(f"Metadata fetch failed for PMID {pmid}: {resp.status_code}")
            return {"pmid": pmid, "doi": None}

        root = etree.fromstring(resp.content)
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

    if text_result["fulltext"]:
        metrics.fulltext_retrieved += 1
        logger.info(f"  Retrieved full text from {text_result['source']}")
    else:
        metrics.abstract_only += 1
        logger.info("  Using abstract only")

    # Skip if no text available
    text = text_result.get("text")
    if not text:
        logger.warning(f"  No text available for PMID {pmid}, skipping")
        return {"genes": [], "fulltext": False, "source": "none"}

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
    validated_genes: list[GeneEntry] = []
    validation_tasks = [validate_gene_entry(gene, config=config) for gene in genes]
    results = await asyncio.gather(*validation_tasks, return_exceptions=True)

    for gene, result in zip(genes, results, strict=True):
        if isinstance(result, Exception):
            logger.error(f"  Validation error for gene {gene.gene_symbol}: {result}")
            metrics.genes_rejected += 1
        elif result.is_valid:
            validated_genes.append(result.normalized_data)
            metrics.genes_validated += 1
        else:
            metrics.genes_rejected += 1
            logger.debug(f"  Gene rejected: {result.errors}")

    return {
        "genes": validated_genes,
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
                fulltext=result["fulltext"],
                source=result["source"],
                processing_time=duration,
            )
        except Exception as e:
            logger.error(f"Error processing PMID {pmid}: {e}")
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
) -> PipelineMetrics:
    """Run the complete data pipeline.

    Args:
        days_back: Number of days to look back (1-3650).
        dry_run: If True, skip database writes.
        test_mode: If True, skip LLM extraction.
        config: Pipeline configuration (uses defaults if None).

    Returns:
        PipelineMetrics with run statistics.

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

    # Set up rate limiter
    rate_limiter = AsyncRateLimiter(rpm=config.rpm_limit, tpm=config.tpm_limit)

    pipeline_start_time = time.monotonic()

    logger.info(f"Starting SVD Dashboard pipeline (looking back {days_back} days)")
    logger.info(
        f"Config: model={config.llm_model}, "
        f"concurrency={config.max_concurrent_papers}, "
        f"RPM={config.rpm_limit}, TPM={config.tpm_limit}"
    )

    try:
        # Step 1: Search PubMed for recent papers
        logger.info("Step 1: Searching PubMed for recent SVD genetic papers...")
        all_pmids = search_recent_papers(days_back)
        logger.info(f"  Found {len(all_pmids)} papers matching SVD genetic criteria")

        if not all_pmids:
            logger.info("No new papers found. Pipeline complete.")
            return metrics

        # Step 2: Filter out already-processed papers
        logger.info("Step 2: Filtering already-processed papers...")
        if dry_run or test_mode:
            existing_pmids: set[str] = set()
            logger.info("  Skipping PMID deduplication (dry-run/test mode)")
        else:
            try:
                existing_pmids = await get_existing_pmids()
            except Exception as e:
                # Table might not exist yet, treat as empty
                logger.warning(f"  Could not fetch existing PMIDs: {e}")
                existing_pmids = set()

        new_pmids = filter_new_pmids(all_pmids, existing_pmids)
        logger.info(f"  {len(new_pmids)} new papers to process")

        if not new_pmids:
            logger.info("All papers already processed. Pipeline complete.")
            return metrics

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
            return metrics

        # Step 3: Process papers concurrently
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
        batch_warnings: list[str] = []
        if all_genes:
            batch_warnings = batch_validate(all_genes)
            for warning in batch_warnings:
                logger.warning(f"  Batch check: {warning}")

        if dry_run:
            logger.info("Dry run mode - skipping database merge")

            total_duration = time.monotonic() - pipeline_start_time
            run_data = build_run_data(
                metrics,
                results,
                all_genes,
                None,
                batch_warnings,
                config,
                days_back,
                dry_run,
                len(all_pmids),
                len(new_pmids),
                total_duration,
            )
            report_path = write_comprehensive_report(run_data, LOG_DIR)
            logger.info(f"JSON report written to: {report_path}")
            print_rich_summary(run_data)
            send_pipeline_summary_email(run_data, config)

            return metrics

        # Step 4: Merge into database
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

        # Comprehensive report + rich summary
        total_duration = time.monotonic() - pipeline_start_time
        run_data = build_run_data(
            metrics,
            results,
            all_genes,
            gene_result,
            batch_warnings,
            config,
            days_back,
            dry_run,
            len(all_pmids),
            len(new_pmids),
            total_duration,
        )
        report_path = write_comprehensive_report(run_data, LOG_DIR)
        logger.info(f"JSON report written to: {report_path}")
        print_rich_summary(run_data)

        # Notify admin with pipeline summary
        send_pipeline_summary_email(run_data, config)

        return metrics

    finally:
        # Cleanup all shared resources
        await _close_metadata_client()
        await close_http_client()
        await close_validation_client()
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

    pipeline_start_time = time.monotonic()

    logger.info(f"Starting local PDF pipeline: {len(pdf_files)} files in {pdf_dir}")
    logger.info(
        f"Config: model={config.llm_model}, "
        f"validation={'disabled' if skip_validation else 'enabled'}"
    )

    results: list[PaperResult] = []
    all_genes: list[GeneEntry] = []

    try:
        semaphore = asyncio.Semaphore(config.max_concurrent_papers)
        progress = {"current": 0, "total": len(pdf_files)}

        async def _process_pdf(idx: int, pdf_path: Path) -> PaperResult:
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
                    text = await asyncio.to_thread(parse_local_pdf, pdf_path)

                    if not text:
                        logger.warning(f"  No text extracted from {pdf_path.name}")
                        return PaperResult(
                            pmid=file_id,
                            error="empty or corrupt PDF",
                            processing_time=time.monotonic() - start_time,
                        )

                    # LLM extraction
                    genes, token_usage = await extract_from_paper(
                        text, file_id, config=config, rate_limiter=rate_limiter
                    )

                    # Update metrics safely (single-threaded event loop)
                    metrics.genes_extracted += len(genes)
                    metrics.token_usage += token_usage

                    # Set identifier
                    for gene in genes:
                        gene.pmid = file_id

                    logger.info(f"  Extracted {len(genes)} genes from {pdf_path.name}")

                    # Validation
                    if skip_validation:
                        validated_genes = genes
                        metrics.genes_validated += len(genes)
                    else:
                        validated_genes = []
                        # Validate genes concurrently for this paper
                        validation_tasks = [
                            validate_gene_entry(gene, config=config) for gene in genes
                        ]
                        val_results = await asyncio.gather(
                            *validation_tasks, return_exceptions=True
                        )

                        for gene, result in zip(genes, val_results, strict=True):
                            if isinstance(result, Exception):
                                logger.error(
                                    f"  Validation error for {gene.gene_symbol}: "
                                    f"{result}"
                                )
                                metrics.genes_rejected += 1
                            elif result.is_valid:
                                validated_genes.append(result.normalized_data)
                                metrics.genes_validated += 1
                            else:
                                metrics.genes_rejected += 1
                                logger.debug(f"  Gene rejected: {result.errors}")

                    metrics.papers_processed += 1
                    metrics.fulltext_retrieved += 1

                    return PaperResult(
                        pmid=file_id,
                        genes=validated_genes,
                        fulltext=True,
                        source="local_pdf",
                        processing_time=time.monotonic() - start_time,
                    )

                except Exception as e:
                    logger.error(f"Error processing {pdf_path.name}: {e}")
                    return PaperResult(
                        pmid=file_id,
                        error=str(e),
                        processing_time=time.monotonic() - start_time,
                    )

        async with asyncio.TaskGroup() as tg:
            tasks = [
                tg.create_task(_process_pdf(idx, pdf_path))
                for idx, pdf_path in enumerate(pdf_files, 1)
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
        run_data = build_local_pdf_run_data(
            metrics,
            results,
            all_genes,
            batch_warnings,
            config,
            pdf_dir,
            skip_validation,
            total_duration,
        )
        report_path = write_comprehensive_report(run_data, LOG_DIR)
        logger.info(f"JSON report written to: {report_path}")
        print_rich_summary(run_data)
        send_pipeline_summary_email(run_data, config)

    finally:
        await close_validation_client()
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
            pmid = _validate_pmid(stripped)
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

    pipeline_start_time = time.monotonic()

    logger.info(f"Starting PMID pipeline: {len(pmids)} PMIDs from {pmid_file}")
    logger.info(
        f"Config: model={config.llm_model}, "
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
                    genes, token_usage = await extract_from_paper(
                        text, pmid, config=config, rate_limiter=rate_limiter
                    )
                    metrics.genes_extracted += len(genes)
                    metrics.token_usage += token_usage

                    for gene in genes:
                        gene.pmid = pmid

                    logger.info(f"  Extracted {len(genes)} genes")

                    # Validation
                    if skip_validation:
                        validated_genes = genes
                        metrics.genes_validated += len(genes)
                    else:
                        validated_genes = []
                        validation_tasks = [
                            validate_gene_entry(gene, config=config) for gene in genes
                        ]
                        val_results = await asyncio.gather(
                            *validation_tasks, return_exceptions=True
                        )
                        for gene, result in zip(genes, val_results, strict=True):
                            if isinstance(result, Exception):
                                logger.error(
                                    f"  Validation error for "
                                    f"{gene.gene_symbol}: {result}"
                                )
                                metrics.genes_rejected += 1
                            elif result.is_valid:
                                validated_genes.append(result.normalized_data)
                                metrics.genes_validated += 1
                            else:
                                metrics.genes_rejected += 1
                                logger.debug(f"  Gene rejected: {result.errors}")

                    if is_fulltext:
                        metrics.fulltext_retrieved += 1
                    else:
                        metrics.abstract_only += 1
                    metrics.papers_processed += 1

                    return PaperResult(
                        pmid=pmid,
                        genes=validated_genes,
                        fulltext=is_fulltext,
                        source=source,
                        processing_time=time.monotonic() - start_time,
                    )
                except Exception as e:
                    logger.error(f"Error processing PMID {pmid}: {e}")
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
            all_genes,
            batch_warnings,
            config,
            pmid_file,
            skip_validation,
            total_duration,
        )
        report_path = write_comprehensive_report(run_data, LOG_DIR)
        logger.info(f"JSON report written to: {report_path}")
        print_rich_summary(run_data)
        send_pipeline_summary_email(run_data, config)

    finally:
        await _close_metadata_client()
        await close_http_client()
        await close_validation_client()
        clear_gene_cache()


async def run_external_data_sync() -> None:
    """Sync all external data sources for dashboard refresh."""
    from pipeline.external_data_sync import sync_all_external_data

    logger.info("Starting external data sync...")
    try:
        result = await sync_all_external_data()
        logger.info(LOG_SEPARATOR)
        logger.info("External Data Sync Summary:")
        logger.info(result.summary())
        logger.info(LOG_SEPARATOR)
    finally:
        await Database.close()


def main() -> None:
    """CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args()

    # Mutual exclusivity checks
    if args.local_pdfs:
        if args.sync_external_data:
            parser.error("--local-pdfs cannot be combined with --sync-external-data")
        if args.test_mode:
            parser.error("--local-pdfs cannot be combined with --test-mode")
        if args.days_back != 7:
            parser.error("--local-pdfs cannot be combined with --days-back")
        if args.pmids:
            parser.error("--local-pdfs cannot be combined with --pmids")

    if args.pmids:
        if args.sync_external_data:
            parser.error("--pmids cannot be combined with --sync-external-data")
        if args.test_mode:
            parser.error("--pmids cannot be combined with --test-mode")
        if args.days_back != 7:
            parser.error("--pmids cannot be combined with --days-back")

    if args.skip_validation and not (args.local_pdfs or args.pmids):
        parser.error("--skip-validation requires --local-pdfs or --pmids")

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
        elif args.sync_external_data:
            asyncio.run(run_external_data_sync())
        else:
            asyncio.run(
                run_pipeline(
                    days_back=args.days_back,
                    dry_run=args.dry_run,
                    test_mode=args.test_mode,
                    config=config,
                )
            )
    except (ValueError, FileNotFoundError) as e:
        logger.error(f"Invalid argument: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Pipeline interrupted by user")
        sys.exit(130)


if __name__ == "__main__":
    main()
