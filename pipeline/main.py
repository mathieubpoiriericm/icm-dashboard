#!/Library/Frameworks/Python.framework/Versions/3.14/bin/python3
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
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Final, TypedDict

import httpx
from lxml import etree  # type: ignore[import-untyped]

# Add project root to path for imports when running as script
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load environment variables from .env file
from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

from pipeline.config import PipelineConfig  # noqa: E402
from pipeline.pubmed_search import search_recent_papers, filter_new_pmids  # noqa: E402
from pipeline.pdf_retrieval import get_fulltext, close_http_client  # noqa: E402
from pipeline.llm_extraction import GeneEntry, extract_from_paper  # noqa: E402
from pipeline.validation import validate_gene_entry, close_validation_client, clear_gene_cache  # noqa: E402
from pipeline.data_merger import merge_gene_entries  # noqa: E402
from pipeline.database import Database, get_existing_pmids, record_processed_pmids_batch, reset_sequence  # noqa: E402
from pipeline.quality_metrics import PipelineMetrics, TokenUsage  # noqa: E402
from pipeline.rate_limiter import AsyncRateLimiter  # noqa: E402
from pipeline.batch_validation import batch_validate  # noqa: E402
from pipeline.report import build_run_data, write_comprehensive_report, print_rich_summary  # noqa: E402

import os  # noqa: E402

# --- Constants ---
LOG_SEPARATOR: Final[str] = "=" * 50
PMID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^\d{1,8}$")

# Configure logging
LOG_DIR = PROJECT_ROOT / "logs"
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
    validation_tasks = [
        validate_gene_entry(gene, config=config) for gene in genes
    ]
    results = await asyncio.gather(*validation_tasks, return_exceptions=True)

    for gene, result in zip(genes, results):
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
        try:
            result = await process_paper(
                pmid, metrics, config=config, rate_limiter=rate_limiter
            )
            return PaperResult(
                pmid=pmid,
                genes=result["genes"],
                fulltext=result["fulltext"],
                source=result["source"],
            )
        except Exception as e:
            logger.error(f"Error processing PMID {pmid}: {e}")
            return PaperResult(pmid=pmid, error=str(e))


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
                    pmid, metrics, semaphore, progress,
                    config=config, rate_limiter=rate_limiter,
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
            f"days_back must be between {config.min_days_back} and {config.max_days_back}, "
            f"got {days_back}"
        )

    metrics = PipelineMetrics()

    # Set up database config
    Database.set_config(config)

    # Set up rate limiter
    rate_limiter = AsyncRateLimiter(rpm=config.rpm_limit, tpm=config.tpm_limit)

    logger.info(f"Starting SVD Dashboard pipeline (looking back {days_back} days)")
    logger.info(
        f"Config: model={config.llm_model}, concurrency={config.max_concurrent_papers}, "
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
            logger.info("Test mode enabled - skipping LLM extraction and database merge")
            logger.info(f"  Would process {len(new_pmids)} papers:")
            for pmid in new_pmids[:config.test_mode_preview_count]:
                logger.info(f"    PMID: {pmid}")
            if len(new_pmids) > config.test_mode_preview_count:
                logger.info(f"    ... and {len(new_pmids) - config.test_mode_preview_count} more")
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

            run_data = build_run_data(
                metrics, results, all_genes, None, batch_warnings,
                config, days_back, dry_run, len(all_pmids), len(new_pmids),
            )
            report_path = write_comprehensive_report(run_data, LOG_DIR)
            logger.info(f"JSON report written to: {report_path}")
            print_rich_summary(run_data)

            return metrics

        # Step 4: Merge into database
        logger.info("Step 4: Merging validated data into database...")

        # Reset sequences to avoid primary key conflicts
        await reset_sequence("genes")

        gene_result: dict[str, int] | None = None
        if all_genes:
            gene_result = await merge_gene_entries(all_genes)
            logger.info(
                f"  Genes: {gene_result['inserted']} inserted, {gene_result['updated']} updated"
            )

        # Step 5: Record processed PMIDs AFTER successful merge
        # This ensures PMIDs are only marked processed when genes are
        # actually written, preventing data loss on merge failure.
        pmid_records = [
            (r.pmid, r.fulltext, r.source, len(r.genes))
            for r in successful_results
        ]
        recorded = await record_processed_pmids_batch(pmid_records)
        logger.info(f"  Recorded {recorded} processed PMIDs")

        # Comprehensive report + rich summary
        run_data = build_run_data(
            metrics, results, all_genes, gene_result, batch_warnings,
            config, days_back, dry_run, len(all_pmids), len(new_pmids),
        )
        report_path = write_comprehensive_report(run_data, LOG_DIR)
        logger.info(f"JSON report written to: {report_path}")
        print_rich_summary(run_data)

        return metrics

    finally:
        # Cleanup all shared resources
        await _close_metadata_client()
        await close_http_client()
        await close_validation_client()
        await Database.close()
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
        help="Run without LLM extraction or database merge (for testing search/retrieval only)",
    )
    parser.add_argument(
        "--sync-external-data",
        action="store_true",
        help="Sync external data (NCBI, UniProt, PubMed) for all genes in database",
    )

    args = parser.parse_args()

    config = PipelineConfig()

    try:
        if args.sync_external_data:
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
    except ValueError as e:
        logger.error(f"Invalid argument: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Pipeline interrupted by user")
        sys.exit(130)


if __name__ == "__main__":
    main()
