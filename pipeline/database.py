"""Async PostgreSQL database operations for the SVD pipeline.

Provides connection pooling, batch operations, and safe SQL execution.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Final

import asyncpg

logger = logging.getLogger(__name__)

# --- Configuration ---
DB_HOST: Final[str | None] = os.getenv("DB_HOST")
DB_NAME: Final[str | None] = os.getenv("DB_NAME")
DB_USER: Final[str | None] = os.getenv("DB_USER")
DB_PORT: Final[int] = int(os.getenv("DB_PORT", "5432"))
DB_PASSWORD: Final[str | None] = os.getenv("DB_PASSWORD")

# Pool settings
DEFAULT_POOL_MIN_SIZE: Final[int] = 2
DEFAULT_POOL_MAX_SIZE: Final[int] = 10
DEFAULT_COMMAND_TIMEOUT: Final[float] = 60.0

# Whitelist of allowed tables/columns for dynamic SQL (prevents SQL injection)
ALLOWED_TABLES: Final[frozenset[str]] = frozenset({"genes", "pubmed_refs"})
ALLOWED_COLUMNS: Final[frozenset[str]] = frozenset({"id"})


class DatabaseConfigError(Exception):
    """Raised when database configuration is invalid."""


class Database:
    """Async database connection pool manager using singleton pattern."""

    __slots__ = ()
    _pool: asyncpg.Pool | None = None

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
            # Validate required config
            missing = [
                name
                for name, val in [
                    ("DB_HOST", DB_HOST),
                    ("DB_NAME", DB_NAME),
                    ("DB_USER", DB_USER),
                    ("DB_PASSWORD", DB_PASSWORD),
                ]
                if not val
            ]
            if missing:
                raise DatabaseConfigError(
                    f"Missing required database environment variables: {missing}"
                )

            cls._pool = await asyncpg.create_pool(
                host=DB_HOST,
                port=DB_PORT,
                user=DB_USER,
                password=DB_PASSWORD,
                database=DB_NAME,
                min_size=DEFAULT_POOL_MIN_SIZE,
                max_size=DEFAULT_POOL_MAX_SIZE,
                command_timeout=DEFAULT_COMMAND_TIMEOUT,
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
        safe_seq = await conn.fetchval("SELECT quote_literal($1)", f"{table}_{column}_seq")

        await conn.execute(f"""
            SELECT setval({safe_seq}, COALESCE(
                (SELECT MAX({safe_column}) FROM {safe_table}), 0
            ) + 1, false)
        """)


async def insert_gene(gene_data: dict[str, Any]) -> bool:
    """Insert a new gene entry.

    Args:
        gene_data: Dictionary with gene fields.

    Returns:
        True if successful.
    """
    async with Database.connection() as conn:
        await conn.execute(
            """
            INSERT INTO genes (
                protein, gene, chromosomal_location, gwas_trait,
                mendelian_randomization, evidence_from_other_omics_studies,
                link_to_monogenetic_disease, brain_cell_types,
                affected_pathway, "references"
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        """,
            gene_data.get("protein"),
            gene_data.get("gene"),
            gene_data.get("chromosomal_location"),
            gene_data.get("gwas_trait"),
            gene_data.get("mendelian_randomization"),
            gene_data.get("evidence_from_other_omics_studies"),
            gene_data.get("link_to_monogenetic_disease"),
            gene_data.get("brain_cell_types"),
            gene_data.get("affected_pathway"),
            gene_data.get("references"),
        )
    return True


async def update_gene(gene_data: dict[str, Any]) -> bool:
    """Update an existing gene entry.

    Only updates fields that may change between pipeline runs:
    gwas_trait, evidence_from_other_omics_studies, references.
    References are appended with "; " separator to preserve provenance.

    Args:
        gene_data: Dictionary with gene fields.

    Returns:
        True if successful.
    """
    async with Database.connection() as conn:
        await conn.execute(
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
            gene_data.get("gwas_trait"),
            gene_data.get("evidence_from_other_omics_studies"),
            gene_data.get("references"),
            gene_data.get("gene"),
        )
    return True


async def insert_genes_batch(genes: list[dict[str, Any]]) -> int:
    """Batch insert multiple gene entries using executemany.

    Args:
        genes: List of gene data dictionaries.

    Returns:
        Number of genes inserted.
    """
    if not genes:
        return 0

    async with Database.connection() as conn:
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
                for g in genes
            ],
        )
    return len(genes)


async def update_genes_batch(genes: list[dict[str, Any]]) -> int:
    """Batch update multiple gene entries.

    Args:
        genes: List of gene data dictionaries with 'gene' key for matching.

    Returns:
        Number of genes updated.
    """
    if not genes:
        return 0

    async with Database.connection() as conn:
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
                for g in genes
            ],
        )
    return len(genes)


async def record_processed_pmid(
    pmid: str,
    fulltext_available: bool = False,
    source: str = "abstract",
    genes_extracted: int = 0,
) -> bool:
    """Record a processed PMID to avoid reprocessing.

    Args:
        pmid: PubMed ID.
        fulltext_available: Whether full text was retrieved.
        source: Source of text (pmc, unpaywall, abstract).
        genes_extracted: Number of genes extracted from this paper.

    Returns:
        True if successful.
    """
    async with Database.connection() as conn:
        await conn.execute(
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
            pmid,
            fulltext_available,
            source,
            genes_extracted,
        )
    return True
