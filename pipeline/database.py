"""Async PostgreSQL database operations for the SVD pipeline.

Provides connection pooling, batch operations, and safe SQL execution.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg

from pipeline.config import ALLOWED_COLUMNS, ALLOWED_TABLES, PipelineConfig

logger = logging.getLogger(__name__)


class DatabaseConfigError(Exception):
    """Raised when database configuration is invalid."""


class Database:
    """Async database connection pool manager using singleton pattern."""

    __slots__ = ()
    _pool: asyncpg.Pool | None = None
    _config: PipelineConfig | None = None

    @classmethod
    def set_config(cls, config: PipelineConfig) -> None:
        """Set the pipeline config for pool sizing parameters."""
        cls._config = config

    @classmethod
    async def get_pool(cls) -> asyncpg.Pool:
        """Get or create the database connection pool.

        Returns:
            The shared asyncpg connection pool.

        Raises:
            DatabaseConfigError: If required environment variables are missing.
            asyncpg.PostgresError: If connection fails.
        """
        if cls._pool is None:
            db_host = os.getenv("DB_HOST")
            db_name = os.getenv("DB_NAME")
            db_user = os.getenv("DB_USER")
            db_password = os.getenv("DB_PASSWORD")
            db_port_raw = os.getenv("DB_PORT", "5432")
            try:
                db_port = int(db_port_raw)
            except ValueError:
                raise DatabaseConfigError(
                    f"DB_PORT must be an integer, got {db_port_raw!r}"
                ) from None

            # Validate required config
            missing = [
                name
                for name, val in [
                    ("DB_HOST", db_host),
                    ("DB_NAME", db_name),
                    ("DB_USER", db_user),
                    ("DB_PASSWORD", db_password),
                ]
                if not val
            ]
            if missing:
                raise DatabaseConfigError(
                    f"Missing required database environment variables: {missing}"
                )

            cfg = cls._config or PipelineConfig()

            cls._pool = await asyncpg.create_pool(
                host=db_host,
                port=db_port,
                user=db_user,
                password=db_password,
                database=db_name,
                min_size=cfg.db_pool_min_size,
                max_size=cfg.db_pool_max_size,
                command_timeout=cfg.db_command_timeout,
            )
        return cls._pool

    @classmethod
    async def close(cls) -> None:
        """Close the connection pool."""
        if cls._pool:
            await cls._pool.close()
            cls._pool = None

    @classmethod
    @asynccontextmanager
    async def connection(cls) -> AsyncIterator[asyncpg.Connection]:
        """Acquire a connection from the pool with automatic release.

        Yields:
            An asyncpg connection that is automatically released on exit.
        """
        pool = await cls.get_pool()
        async with pool.acquire() as conn:
            yield conn


async def get_existing_genes() -> set[str]:
    """Fetch all gene symbols currently in the database.

    Returns:
        Set of uppercase gene symbols.
    """
    async with Database.connection() as conn:
        rows = await conn.fetch("SELECT UPPER(gene) as gene FROM genes")
        return {row["gene"] for row in rows}


async def get_existing_pmids() -> set[str]:
    """Fetch all PMIDs already processed.

    Returns:
        Set of PMID strings.
    """
    async with Database.connection() as conn:
        rows = await conn.fetch("SELECT pmid FROM pubmed_refs")
        return {row["pmid"] for row in rows}


async def reset_sequence(table: str, column: str = "id") -> None:
    """Reset a table's sequence to avoid primary key conflicts.

    Uses whitelist validation to prevent SQL injection.

    Args:
        table: Table name (must be in ALLOWED_TABLES).
        column: Column name (must be in ALLOWED_COLUMNS).

    Raises:
        ValueError: If table or column is not in the allowed whitelist.
    """
    # Whitelist validation - prevents SQL injection
    if table not in ALLOWED_TABLES:
        raise ValueError(f"Table '{table}' not in allowed list: {ALLOWED_TABLES}")
    if column not in ALLOWED_COLUMNS:
        raise ValueError(f"Column '{column}' not in allowed list: {ALLOWED_COLUMNS}")

    async with Database.connection() as conn:
        # Use quote_ident for defense-in-depth (even after whitelist validation)
        safe_table = await conn.fetchval("SELECT quote_ident($1)", table)
        safe_column = await conn.fetchval("SELECT quote_ident($1)", column)
        safe_seq = await conn.fetchval(
            "SELECT quote_literal($1)", f"{table}_{column}_seq"
        )

        if safe_table is None or safe_column is None or safe_seq is None:
            raise RuntimeError(
                f"quote_ident/quote_literal returned NULL for {table}.{column}"
            )

        await conn.execute(f"""
            SELECT setval({safe_seq}, COALESCE(
                (SELECT MAX({safe_column}) FROM {safe_table}), 0
            ) + 1, false)
        """)


async def merge_genes_transactional(
    to_insert: list[dict[str, Any]],
    to_update: list[dict[str, Any]],
) -> tuple[int, int]:
    """Atomically insert and update genes in a single transaction.

    If any operation fails, the entire batch is rolled back, preventing
    inconsistent state from partial writes.

    Args:
        to_insert: List of gene data dictionaries to insert.
        to_update: List of gene data dictionaries to update.

    Returns:
        Tuple of (inserted_count, updated_count).
    """
    if not to_insert and not to_update:
        return 0, 0

    async with Database.connection() as conn, conn.transaction():
        if to_insert:
            await conn.executemany(
                """
                    INSERT INTO genes (
                        protein, gene, chromosomal_location, gwas_trait,
                        mendelian_randomization, evidence_from_other_omics_studies,
                        link_to_monogenetic_disease, brain_cell_types,
                        affected_pathway, "references"
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    """,
                [
                    (
                        g.get("protein"),
                        g.get("gene"),
                        g.get("chromosomal_location"),
                        g.get("gwas_trait"),
                        g.get("mendelian_randomization"),
                        g.get("evidence_from_other_omics_studies"),
                        g.get("link_to_monogenetic_disease"),
                        g.get("brain_cell_types"),
                        g.get("affected_pathway"),
                        g.get("references"),
                    )
                    for g in to_insert
                ],
            )
        if to_update:
            await conn.executemany(
                """
                    UPDATE genes SET
                        gwas_trait = $1,
                        evidence_from_other_omics_studies = $2,
                        "references" = CASE
                            WHEN "references" LIKE '%' || $3 || '%' THEN "references"
                            ELSE "references" || '; ' || $3
                        END,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE UPPER(gene) = UPPER($4)
                    """,
                [
                    (
                        g.get("gwas_trait"),
                        g.get("evidence_from_other_omics_studies"),
                        g.get("references"),
                        g.get("gene"),
                    )
                    for g in to_update
                ],
            )

    return len(to_insert), len(to_update)


async def record_processed_pmids_batch(
    records: list[tuple[str, bool, str, int]],
) -> int:
    """Batch record processed PMIDs to avoid reprocessing.

    Args:
        records: List of (pmid, fulltext_available, source, genes_extracted) tuples.

    Returns:
        Number of PMIDs recorded.
    """
    if not records:
        return 0

    async with Database.connection() as conn:
        await conn.executemany(
            """
            INSERT INTO pubmed_refs (
                pmid, fulltext_available, source, genes_extracted
            ) VALUES ($1, $2, $3, $4)
            ON CONFLICT (pmid) DO UPDATE SET
                fulltext_available = EXCLUDED.fulltext_available,
                source = EXCLUDED.source,
                genes_extracted = EXCLUDED.genes_extracted,
                processed_at = CURRENT_TIMESTAMP
            """,
            records,
        )
    return len(records)


# =============================================================================
# NCBI Gene Info Cache Operations
# =============================================================================


async def get_cached_ncbi_genes(gene_symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Get cached NCBI gene info for given symbols.

    Args:
        gene_symbols: List of gene symbols to look up.

    Returns:
        Dict mapping gene_symbol -> {ncbi_uid, description, aliases}.
    """
    if not gene_symbols:
        return {}

    async with Database.connection() as conn:
        rows = await conn.fetch(
            """
            SELECT gene_symbol, ncbi_uid, description, aliases
            FROM ncbi_gene_info
            WHERE gene_symbol = ANY($1)
            """,
            gene_symbols,
        )
        return {
            row["gene_symbol"]: {
                "ncbi_uid": row["ncbi_uid"],
                "description": row["description"],
                "aliases": row["aliases"],
            }
            for row in rows
        }


async def upsert_ncbi_genes_batch(genes: list[Any]) -> int:
    """Batch upsert NCBI gene info.

    Args:
        genes: List of NCBIGeneInfo objects.

    Returns:
        Number of genes upserted.
    """
    if not genes:
        return 0

    async with Database.connection() as conn:
        await conn.executemany(
            """
            INSERT INTO ncbi_gene_info (gene_symbol, ncbi_uid, description, aliases)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (gene_symbol) DO UPDATE SET
                ncbi_uid = EXCLUDED.ncbi_uid,
                description = EXCLUDED.description,
                aliases = EXCLUDED.aliases,
                updated_at = CURRENT_TIMESTAMP
            """,
            [(g.gene_symbol, g.ncbi_uid, g.description, g.aliases) for g in genes],
        )
    return len(genes)


# =============================================================================
# UniProt Info Cache Operations
# =============================================================================


async def get_cached_uniprot_info(gene_symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Get cached UniProt info for given gene symbols.

    Args:
        gene_symbols: List of gene symbols to look up.

    Returns:
        Dict mapping gene_symbol -> UniProt info dict.
    """
    if not gene_symbols:
        return {}

    async with Database.connection() as conn:
        rows = await conn.fetch(
            """
            SELECT gene_symbol, accession, protein_name,
                   biological_process, molecular_function, cellular_component, url
            FROM uniprot_info
            WHERE gene_symbol = ANY($1)
            """,
            gene_symbols,
        )
        return {
            row["gene_symbol"]: {
                "accession": row["accession"],
                "protein_name": row["protein_name"],
                "biological_process": row["biological_process"],
                "molecular_function": row["molecular_function"],
                "cellular_component": row["cellular_component"],
                "url": row["url"],
            }
            for row in rows
        }


async def upsert_uniprot_batch(infos: list[Any]) -> int:
    """Batch upsert UniProt info.

    Args:
        infos: List of UniProtInfo objects.

    Returns:
        Number of entries upserted.
    """
    if not infos:
        return 0

    async with Database.connection() as conn:
        await conn.executemany(
            """
            INSERT INTO uniprot_info (
                gene_symbol, accession, protein_name,
                biological_process, molecular_function, cellular_component, url
            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (gene_symbol) DO UPDATE SET
                accession = EXCLUDED.accession,
                protein_name = EXCLUDED.protein_name,
                biological_process = EXCLUDED.biological_process,
                molecular_function = EXCLUDED.molecular_function,
                cellular_component = EXCLUDED.cellular_component,
                url = EXCLUDED.url,
                updated_at = CURRENT_TIMESTAMP
            """,
            [
                (
                    i.gene_symbol,
                    i.accession,
                    i.protein_name,
                    i.biological_process,
                    i.molecular_function,
                    i.cellular_component,
                    i.url,
                )
                for i in infos
            ],
        )
    return len(infos)


# =============================================================================
# PubMed Citations Cache Operations
# =============================================================================


async def get_cached_pubmed_citations(pmids: list[str]) -> dict[str, dict[str, Any]]:
    """Get cached PubMed citations for given PMIDs.

    Args:
        pmids: List of PubMed IDs to look up.

    Returns:
        Dict mapping pmid -> citation info dict.
    """
    if not pmids:
        return {}

    async with Database.connection() as conn:
        rows = await conn.fetch(
            """
            SELECT pmid, authors, title, journal, publication_date, doi, formatted_ref
            FROM pubmed_citations
            WHERE pmid = ANY($1)
            """,
            pmids,
        )
        return {
            row["pmid"]: {
                "authors": row["authors"],
                "title": row["title"],
                "journal": row["journal"],
                "publication_date": row["publication_date"],
                "doi": row["doi"],
                "formatted_ref": row["formatted_ref"],
            }
            for row in rows
        }


async def upsert_pubmed_citations_batch(citations: list[Any]) -> int:
    """Batch upsert PubMed citations.

    Args:
        citations: List of PubMedCitation objects.

    Returns:
        Number of citations upserted.
    """
    if not citations:
        return 0

    async with Database.connection() as conn:
        await conn.executemany(
            """
            INSERT INTO pubmed_citations (
                pmid, authors, title, journal, publication_date, doi, formatted_ref
            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (pmid) DO UPDATE SET
                authors = EXCLUDED.authors,
                title = EXCLUDED.title,
                journal = EXCLUDED.journal,
                publication_date = EXCLUDED.publication_date,
                doi = EXCLUDED.doi,
                formatted_ref = EXCLUDED.formatted_ref,
                updated_at = CURRENT_TIMESTAMP
            """,
            [
                (
                    c.pmid,
                    c.authors,
                    c.title,
                    c.journal,
                    c.publication_date,
                    c.doi,
                    c.formatted_ref,
                )
                for c in citations
            ],
        )
    return len(citations)
