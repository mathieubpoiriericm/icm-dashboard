#!/Users/mathieupoirier/miniconda3/bin/python
"""
Main entry point for the SVD Dashboard data pipeline.

This script orchestrates:
1. PubMed search for new SVD-related papers
2. Full-text/abstract retrieval
3. LLM-based data extraction using Claude
4. Validation against external databases
5. Merging validated data into PostgreSQL

Usage:
    python pipeline/main.py [--days-back N] [--dry-run] [--test-mode]
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import List

# Add project root to path for imports when running as script
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load environment variables from .env file
from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

from pipeline.pubmed_search import search_recent_papers, filter_new_pmids  # noqa: E402
from pipeline.pdf_retrieval import get_fulltext  # noqa: E402
from pipeline.llm_extraction import extract_from_paper  # noqa: E402
from pipeline.validation import validate_gene_entry, validate_trial_entry  # noqa: E402
from pipeline.data_merger import merge_gene_entries, merge_trial_entries  # noqa: E402
from pipeline.database import Database, get_existing_pmids, record_processed_pmid  # noqa: E402
from pipeline.quality_metrics import PipelineMetrics  # noqa: E402

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


async def fetch_paper_metadata(pmid: str) -> dict:
    """Fetch DOI and other metadata for a PMID using NCBI efetch."""
    import httpx
    import os

    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    params = {"db": "pubmed", "id": pmid, "retmode": "xml"}
    api_key = os.getenv("NCBI_API_KEY")
    if api_key:
        params["api_key"] = api_key

    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params=params)
        # Parse XML to extract DOI
        from lxml import etree  # type: ignore[import]

        root = etree.fromstring(resp.content)
        doi_elem = root.find(".//ArticleId[@IdType='doi']")
        doi = doi_elem.text if doi_elem is not None else None
        return {"pmid": pmid, "doi": doi}


async def process_paper(pmid: str, metrics: PipelineMetrics) -> dict:
    """Process a single paper: fetch text, extract data, validate."""
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
    if not text_result.get("text"):
        logger.warning(f"  No text available for PMID {pmid}, skipping")
        return {"genes": [], "trials": [], "fulltext": False, "source": "none"}

    # Extract structured data using LLM
    extracted = extract_from_paper(text_result["text"], pmid)

    genes = extracted.get("genes", [])
    trials = extracted.get("trials", [])

    metrics.genes_extracted += len(genes)
    metrics.trials_extracted += len(trials)

    logger.info(f"  Extracted {len(genes)} genes, {len(trials)} trials")

    # Validate genes
    validated_genes = []
    for gene in genes:
        gene["pmid"] = pmid  # Add source PMID
        result = await validate_gene_entry(gene)
        if result.is_valid:
            validated_genes.append(result.normalized_data)
            metrics.genes_validated += 1
        else:
            metrics.genes_rejected += 1
            logger.debug(f"  Gene rejected: {result.errors}")

    # Validate trials
    validated_trials = []
    for trial in trials:
        result = await validate_trial_entry(trial)
        if result.is_valid:
            validated_trials.append(result.normalized_data)
            metrics.trials_validated += 1
        else:
            metrics.trials_rejected += 1
            logger.debug(f"  Trial rejected: {result.errors}")

    return {
        "genes": validated_genes,
        "trials": validated_trials,
        "fulltext": text_result["fulltext"],
        "source": text_result["source"],
    }


async def run_pipeline(
    days_back: int = 7, dry_run: bool = False, test_mode: bool = False
) -> PipelineMetrics:
    """Run the complete data pipeline."""
    metrics = PipelineMetrics()

    logger.info(f"Starting SVD Dashboard pipeline (looking back {days_back} days)")

    # Step 1: Search PubMed for recent papers
    logger.info("Step 1: Searching PubMed for recent SVD papers...")
    all_pmids = search_recent_papers(days_back)
    logger.info(f"  Found {len(all_pmids)} papers matching SVD criteria")

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
        for pmid in new_pmids[:10]:  # Show first 10
            logger.info(f"    PMID: {pmid}")
        if len(new_pmids) > 10:
            logger.info(f"    ... and {len(new_pmids) - 10} more")
        return metrics

    # Step 3: Process each paper
    logger.info("Step 3: Processing papers...")
    all_genes: List[dict] = []
    all_trials: List[dict] = []

    for pmid in new_pmids:
        try:
            result = await process_paper(pmid, metrics)
            all_genes.extend(result["genes"])
            all_trials.extend(result["trials"])
            metrics.papers_processed += 1

            # Record processed PMID (even in dry run to track what was processed)
            await record_processed_pmid(
                pmid=pmid,
                fulltext_available=result.get("fulltext", False),
                source=result.get("source", "unknown"),
                genes_extracted=len(result["genes"]),
                trials_extracted=len(result["trials"]),
            )
        except Exception as e:
            logger.error(f"  Error processing PMID {pmid}: {e}")
            continue

    logger.info(f"  Processed {metrics.papers_processed} papers")
    logger.info(
        f"  Validated: {metrics.genes_validated} genes, {metrics.trials_validated} trials"
    )

    if dry_run:
        logger.info("Dry run mode - skipping database merge")
        return metrics

    # Step 4: Merge into database
    logger.info("Step 4: Merging validated data into database...")

    if all_genes:
        gene_result = await merge_gene_entries(all_genes)
        logger.info(
            f"  Genes: {gene_result['inserted']} inserted, {gene_result['updated']} updated"
        )

    if all_trials:
        trial_result = await merge_trial_entries(all_trials)
        logger.info(f"  Trials: {trial_result['inserted']} inserted")

    # Cleanup
    await Database.close()

    # Summary
    logger.info("=" * 50)
    logger.info("Pipeline Summary:")
    logger.info(f"  Papers processed: {metrics.papers_processed}")
    logger.info(f"  Full-text retrieved: {metrics.fulltext_retrieved}")
    logger.info(f"  Abstract only: {metrics.abstract_only}")
    logger.info(
        f"  Genes: {metrics.genes_validated}/{metrics.genes_extracted} validated"
    )
    logger.info(
        f"  Trials: {metrics.trials_validated}/{metrics.trials_extracted} validated"
    )
    if metrics.genes_extracted > 0:
        logger.info(f"  Gene acceptance rate: {metrics.gene_acceptance_rate:.1%}")
    logger.info("=" * 50)

    return metrics


def main():
    parser = argparse.ArgumentParser(description="SVD Dashboard data pipeline")
    parser.add_argument(
        "--days-back",
        type=int,
        default=7,
        help="Number of days to look back for new papers (default: 7)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Run without writing to database"
    )
    parser.add_argument(
        "--test-mode",
        action="store_true",
        help="Run without LLM extraction or database merge (for testing search/retrieval only)",
    )

    args = parser.parse_args()

    asyncio.run(
        run_pipeline(
            days_back=args.days_back, dry_run=args.dry_run, test_mode=args.test_mode
        )
    )


if __name__ == "__main__":
    main()
