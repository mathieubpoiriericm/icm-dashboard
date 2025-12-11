"""External data synchronization orchestrator.

Coordinates fetching of NCBI, UniProt, and PubMed data for all genes
in the database, storing results for dashboard consumption.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field

from pipeline.database import Database
from pipeline.ncbi_gene_fetch import (
    clear_ncbi_cache,
    close_ncbi_client,
    sync_ncbi_gene_info,
)
from pipeline.pubmed_citations import (
    clear_pubmed_cache,
    close_pubmed_client,
    extract_pmids_from_text,
    sync_pubmed_citations,
)
from pipeline.uniprot_fetch import (
    clear_uniprot_cache,
    close_uniprot_client,
    sync_uniprot_info,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ExternalDataSyncResult:
    """Combined result from all external data sync operations."""

    ncbi_fetched: int = 0
    ncbi_cached: int = 0
    ncbi_failed: int = 0
    uniprot_fetched: int = 0
    uniprot_cached: int = 0
    uniprot_failed: int = 0
    pubmed_fetched: int = 0
    pubmed_cached: int = 0
    pubmed_failed: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """Return human-readable summary."""
        return (
            f"NCBI: {self.ncbi_fetched} fetched, "
            f"{self.ncbi_cached} cached, "
            f"{self.ncbi_failed} failed\n"
            f"UniProt: {self.uniprot_fetched} fetched, "
            f"{self.uniprot_cached} cached, "
            f"{self.uniprot_failed} failed\n"
            f"PubMed: {self.pubmed_fetched} fetched, "
            f"{self.pubmed_cached} cached, "
            f"{self.pubmed_failed} failed"
        )


async def get_table1_gene_symbols() -> list[str]:
    """Get unique gene symbols from the genes table (Table 1)."""
    async with Database.connection() as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT gene FROM genes WHERE gene IS NOT NULL"
        )
        return [row["gene"] for row in rows]


async def get_table2_gene_symbols() -> list[str]:
    """Get unique gene symbols from the clinical_trials table (Table 2).

    The genetic_target column may contain comma-separated gene lists.
    """
    async with Database.connection() as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT genetic_target "
            "FROM clinical_trials "
            "WHERE genetic_target IS NOT NULL"
        )

    # Extract individual genes from comma-separated lists
    all_genes: set[str] = set()
    for row in rows:
        target = row["genetic_target"]
        if target:
            # Split by comma, semicolon, or slash
            genes = re.split(r"[,;/]", target)
            for gene in genes:
                gene = gene.strip()
                if gene and gene.upper() != "NA" and gene != "-":
                    all_genes.add(gene)

    return sorted(all_genes)


async def get_all_pmids() -> list[str]:
    """Extract all unique PMIDs from the genes.references column."""
    async with Database.connection() as conn:
        rows = await conn.fetch(
            'SELECT "references" FROM genes WHERE "references" IS NOT NULL'
        )

    all_pmids: set[str] = set()
    for row in rows:
        refs = row["references"]
        if refs:
            pmids = extract_pmids_from_text(refs)
            all_pmids.update(pmids)

    return sorted(all_pmids)


_MAX_ERRORS_PER_SOURCE: int = 10


def _append_errors_truncated(
    target: list[str], source: list[str], label: str
) -> None:
    """Append errors from source to target, truncating with a message."""
    if len(source) <= _MAX_ERRORS_PER_SOURCE:
        target.extend(source)
    else:
        target.extend(source[:_MAX_ERRORS_PER_SOURCE])
        suppressed = len(source) - _MAX_ERRORS_PER_SOURCE
        target.append(f"... and {suppressed} more {label} errors suppressed")


async def sync_all_external_data() -> ExternalDataSyncResult:
    """Sync all external data sources for dashboard refresh.

    This function:
    1. Gets all gene symbols from genes and clinical_trials tables
    2. Extracts PMIDs from genes.references column
    3. Syncs NCBI gene info for all genes
    4. Syncs UniProt info for Table 1 genes
    5. Syncs PubMed citations for all PMIDs

    Returns:
        ExternalDataSyncResult with sync statistics.
    """
    result = ExternalDataSyncResult()

    try:
        async with asyncio.timeout(3600):
            # Step 1: Get gene symbols
            logger.info("Collecting gene symbols from database...")
            table1_genes = await get_table1_gene_symbols()
            table2_genes = await get_table2_gene_symbols()
            all_genes = list(dict.fromkeys(table1_genes + table2_genes))  # Deduplicate

            logger.info(f"Found {len(table1_genes)} genes in Table 1")
            logger.info(f"Found {len(table2_genes)} genes in Table 2")
            logger.info(f"Total unique genes: {len(all_genes)}")

            # Step 2: Get PMIDs
            logger.info("Extracting PMIDs from references...")
            pmids = await get_all_pmids()
            logger.info(f"Found {len(pmids)} unique PMIDs")

            # Step 3: Sync NCBI gene info
            logger.info("Syncing NCBI gene info...")
            ncbi_result = await sync_ncbi_gene_info(all_genes)
            result.ncbi_fetched = ncbi_result.fetched
            result.ncbi_cached = ncbi_result.cached
            result.ncbi_failed = ncbi_result.failed
            _append_errors_truncated(result.errors, ncbi_result.errors, "NCBI")

            # Step 4: Sync UniProt info (Table 1 genes only)
            logger.info("Syncing UniProt info...")
            uniprot_result = await sync_uniprot_info(table1_genes)
            result.uniprot_fetched = uniprot_result.fetched
            result.uniprot_cached = uniprot_result.cached
            result.uniprot_failed = uniprot_result.failed
            _append_errors_truncated(result.errors, uniprot_result.errors, "UniProt")

            # Step 5: Sync PubMed citations
            if pmids:
                logger.info("Syncing PubMed citations...")
                pubmed_result = await sync_pubmed_citations(pmids)
                result.pubmed_fetched = pubmed_result.fetched
                result.pubmed_cached = pubmed_result.cached
                result.pubmed_failed = pubmed_result.failed
                _append_errors_truncated(
                    result.errors, pubmed_result.errors, "PubMed"
                )
            else:
                logger.info("No PMIDs to sync")

            logger.info("External data sync complete")
            logger.info(result.summary())

            return result

    except TimeoutError:
        logger.error("External data sync timed out after 1 hour")
        result.errors.append("Sync timed out after 3600s")
        return result

    finally:
        # Cleanup all HTTP clients and caches
        await close_ncbi_client()
        await close_uniprot_client()
        await close_pubmed_client()
        clear_ncbi_cache()
        clear_uniprot_cache()
        clear_pubmed_cache()
