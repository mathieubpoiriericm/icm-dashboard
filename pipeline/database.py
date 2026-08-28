"""Async PostgreSQL database operations for the SVD pipeline.

Provides connection pooling, batch operations, and safe SQL execution.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, ClassVar, cast

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
    _pool_lock: ClassVar[asyncio.Lock] = asyncio.Lock()

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
        if cls._pool is not None:
            return cls._pool
        async with cls._pool_lock:
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
                required = {
                    "DB_HOST": db_host,
                    "DB_NAME": db_name,
                    "DB_USER": db_user,
                    "DB_PASSWORD": db_password,
                }
                missing = [name for name, value in required.items() if not value]
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
        if cls._pool is not None:
            await cls._pool.close()
            cls._pool = None

    @classmethod
    @asynccontextmanager
    async def connection(cls) -> AsyncGenerator[asyncpg.Connection]:
        """Acquire a connection from the pool with automatic release.

        Yields:
            An asyncpg connection that is automatically released on exit.
        """
        pool = await cls.get_pool()
        async with pool.acquire() as conn:
            # asyncpg's proxy exposes the Connection API but its inline types do
            # not model that structural relationship.
            yield cast(asyncpg.Connection, conn)


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

    # "references" dedup invariant: empty/NULL new input keeps the existing
    # value; empty/NULL existing value is replaced; otherwise append with a
    # "; " separator iff the new PMID isn't already present in the string.
    # The COALESCEs guard against NULL || anything = NULL silently dropping
    # the reference.
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
                    ON CONFLICT (gene) DO UPDATE SET
                        protein = CASE
                            WHEN genes.protein IS NULL
                              OR genes.protein = ''
                              OR genes.protein = genes.gene
                            THEN COALESCE(NULLIF(EXCLUDED.protein, ''), genes.protein)
                            ELSE genes.protein
                        END,
                        gwas_trait = (
                            SELECT COALESCE(
                                string_agg(val, ', ' ORDER BY first_ord), ''
                            )
                            FROM (
                                SELECT val, MIN(ord) AS first_ord
                                FROM (
                                    SELECT btrim(value) AS val, ord
                                    FROM unnest(
                                        string_to_array(
                                            COALESCE(genes.gwas_trait, ''),
                                            ','
                                        )
                                    ) WITH ORDINALITY AS t(value, ord)
                                    UNION ALL
                                    SELECT btrim(value) AS val, ord + 100000
                                    FROM unnest(
                                        string_to_array(
                                            COALESCE(EXCLUDED.gwas_trait, ''), ','
                                        )
                                    ) WITH ORDINALITY AS t(value, ord)
                                ) raw
                                WHERE val <> ''
                                GROUP BY val
                            ) dedup
                        ),
                        mendelian_randomization = CASE
                            WHEN genes.mendelian_randomization = 'Y'
                              OR EXCLUDED.mendelian_randomization = 'Y'
                            THEN 'Y'
                            ELSE COALESCE(genes.mendelian_randomization, '')
                        END,
                        evidence_from_other_omics_studies =
                            (
                                SELECT COALESCE(
                                    string_agg(val, ';' ORDER BY first_ord), ''
                                )
                                FROM (
                                    SELECT val, MIN(ord) AS first_ord
                                    FROM (
                                        SELECT btrim(value) AS val, ord
                                        FROM unnest(
                                            string_to_array(
                                                COALESCE(
                                                    genes.evidence_from_other_omics_studies,
                                                    ''
                                                ),
                                                ';'
                                            )
                                        ) WITH ORDINALITY AS t(value, ord)
                                        UNION ALL
                                        SELECT btrim(value) AS val, ord + 100000
                                        FROM unnest(
                                            string_to_array(
                                                COALESCE(
                                                    EXCLUDED.evidence_from_other_omics_studies,
                                                    ''
                                                ),
                                                ';'
                                            )
                                        ) WITH ORDINALITY AS t(value, ord)
                                    ) raw
                                    WHERE val <> ''
                                    GROUP BY val
                                ) dedup
                        ),
                        "references" = (
                            SELECT COALESCE(
                                string_agg(val, '; ' ORDER BY first_ord), ''
                            )
                            FROM (
                                SELECT val, MIN(ord) AS first_ord
                                FROM (
                                    SELECT btrim(value) AS val, ord
                                    FROM unnest(
                                        string_to_array(
                                            COALESCE(genes."references", ''),
                                            ';'
                                        )
                                    ) WITH ORDINALITY AS t(value, ord)
                                    UNION ALL
                                    SELECT btrim(value) AS val, ord + 100000
                                    FROM unnest(
                                        string_to_array(
                                            COALESCE(EXCLUDED."references", ''), ';'
                                        )
                                    ) WITH ORDINALITY AS t(value, ord)
                                ) raw
                                WHERE val <> ''
                                GROUP BY val
                            ) dedup
                        ),
                        updated_at = CURRENT_TIMESTAMP
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
                        protein = CASE
                            WHEN protein IS NULL OR protein = '' OR protein = gene
                            THEN COALESCE(NULLIF($1::text, ''), protein)
                            ELSE protein
                        END,
                        gwas_trait = (
                            SELECT COALESCE(
                                string_agg(val, ', ' ORDER BY first_ord), ''
                            )
                            FROM (
                                SELECT val, MIN(ord) AS first_ord
                                FROM (
                                    SELECT btrim(value) AS val, ord
                                    FROM unnest(
                                        string_to_array(COALESCE(gwas_trait, ''), ',')
                                    ) WITH ORDINALITY AS t(value, ord)
                                    UNION ALL
                                    SELECT btrim(value) AS val, ord + 100000
                                    FROM unnest(
                                        string_to_array(COALESCE($2::text, ''), ',')
                                    ) WITH ORDINALITY AS t(value, ord)
                                ) raw
                                WHERE val <> ''
                                GROUP BY val
                            ) dedup
                        ),
                        mendelian_randomization = CASE
                            WHEN mendelian_randomization = 'Y' OR $3::text = 'Y'
                            THEN 'Y'
                            ELSE COALESCE(mendelian_randomization, '')
                        END,
                        evidence_from_other_omics_studies = (
                            SELECT COALESCE(string_agg(val, ';' ORDER BY first_ord), '')
                            FROM (
                                SELECT val, MIN(ord) AS first_ord
                                FROM (
                                    SELECT btrim(value) AS val, ord
                                    FROM unnest(
                                        string_to_array(
                                            COALESCE(
                                                evidence_from_other_omics_studies,
                                                ''
                                            ),
                                            ';'
                                        )
                                    ) WITH ORDINALITY AS t(value, ord)
                                    UNION ALL
                                    SELECT btrim(value) AS val, ord + 100000
                                    FROM unnest(
                                        string_to_array(COALESCE($4::text, ''), ';')
                                    ) WITH ORDINALITY AS t(value, ord)
                                ) raw
                                WHERE val <> ''
                                GROUP BY val
                            ) dedup
                        ),
                        "references" = (
                            SELECT COALESCE(
                                string_agg(val, '; ' ORDER BY first_ord), ''
                            )
                            FROM (
                                SELECT val, MIN(ord) AS first_ord
                                FROM (
                                    SELECT btrim(value) AS val, ord
                                    FROM unnest(
                                        string_to_array(COALESCE("references", ''), ';')
                                    ) WITH ORDINALITY AS t(value, ord)
                                    UNION ALL
                                    SELECT btrim(value) AS val, ord + 100000
                                    FROM unnest(
                                        string_to_array(COALESCE($5::text, ''), ';')
                                    ) WITH ORDINALITY AS t(value, ord)
                                ) raw
                                WHERE val <> ''
                                GROUP BY val
                            ) dedup
                        ),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE UPPER(gene) = UPPER($6)
                    """,
                [
                    (
                        g.get("protein"),
                        g.get("gwas_trait"),
                        g.get("mendelian_randomization"),
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

    async with Database.connection() as conn, conn.transaction():
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
# Pipeline Run Tracking
# =============================================================================


async def record_pipeline_run(
    run_timestamp: str,
    papers_processed: int,
    fulltext_retrieved: int,
    genes_extracted: int,
    genes_validated: int,
    run_mode: str = "standard",
) -> int:
    """Record a pipeline run's summary statistics.

    Args:
        run_timestamp: ISO-format timestamp of the run.
        papers_processed: Number of papers successfully processed.
        fulltext_retrieved: Number of papers with full text.
        genes_extracted: Total genes extracted by LLM.
        genes_validated: Genes passing validation.
        run_mode: One of 'standard', 'local_pdf', or 'pmid_list'.

    Returns:
        The id of the inserted row.
    """
    async with Database.connection() as conn:
        row_id = await conn.fetchval(
            """
            INSERT INTO pipeline_runs (
                run_timestamp, papers_processed, fulltext_retrieved,
                genes_extracted, genes_validated, run_mode
            ) VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id
            """,
            run_timestamp,
            papers_processed,
            fulltext_retrieved,
            genes_extracted,
            genes_validated,
            run_mode,
        )
    if not isinstance(row_id, int):
        raise RuntimeError("Database did not return an integer pipeline run id")
    logger.info("Recorded pipeline run id=%d (mode=%s)", row_id, run_mode)
    return row_id


# =============================================================================
# NCBI Gene Info Cache Operations
# =============================================================================


async def get_cached_ncbi_genes(
    gene_symbols: list[str],
    max_age_days: int | None = None,
) -> dict[str, dict[str, Any]]:
    """Get cached NCBI gene info for given symbols.

    Args:
        gene_symbols: List of gene symbols to look up.
        max_age_days: If set, only return rows updated within this many days;
            older rows are treated as stale and re-fetched by the caller.

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
            WHERE gene_symbol = ANY($1::text[])
              AND ($2::int IS NULL
                   OR updated_at > NOW() - make_interval(days => $2::int))
            """,
            gene_symbols,
            max_age_days,
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


async def get_cached_uniprot_info(
    gene_symbols: list[str],
    max_age_days: int | None = None,
) -> dict[str, dict[str, Any]]:
    """Get cached UniProt info for given gene symbols.

    Args:
        gene_symbols: List of gene symbols to look up.
        max_age_days: If set, only return rows updated within this many days;
            older rows are treated as stale and re-fetched by the caller.

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
            WHERE gene_symbol = ANY($1::text[])
              AND ($2::int IS NULL
                   OR updated_at > NOW() - make_interval(days => $2::int))
            """,
            gene_symbols,
            max_age_days,
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
            WHERE pmid = ANY($1::text[])
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


# =============================================================================
# Clinical Trials Operations (ClinicalTrials.gov sync)
# =============================================================================


async def upsert_clinical_trials_batch(trials: list[Any]) -> int:
    """Batch upsert clinical trial rows from ClinicalTrials.gov.

    Curator-owned columns (``mechanism_of_action``, ``genetic_target``,
    ``genetic_evidence``, ``svd_population``, ``svd_population_details``)
    are deliberately omitted from the INSERT column list and the ON CONFLICT
    update set. On INSERT they default to NULL; on CONFLICT they remain
    whatever the curator set them to. This preserves curator edits across
    pipeline runs.

    Args:
        trials: List of ClinicalTrialRecord objects (from
            ``pipeline.clinical_trials_fetch``).

    Returns:
        Number of trial rows submitted for upsert.
    """
    if not trials:
        return 0

    # Postgres treats NULLs as distinct in UNIQUE constraints, so rows with
    # a NULL registry_id bypass ON CONFLICT and duplicate on every run.
    # Skip (with a warning) rather than insert duplicates.
    filtered = [t for t in trials if t.registry_id]
    skipped = len(trials) - len(filtered)
    if skipped:
        logger.warning(
            "Skipping %d clinical trial(s) with NULL/empty registry_id "
            "(would bypass ON CONFLICT and duplicate)",
            skipped,
        )
    if not filtered:
        return 0

    async with Database.connection() as conn:
        await conn.executemany(
            """
            INSERT INTO clinical_trials (
                drug, trial_name, registry_id, clinical_trial_phase,
                target_sample_size, estimated_completion_date,
                primary_outcome, sponsor_type
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (registry_id, drug) DO UPDATE SET
                trial_name = EXCLUDED.trial_name,
                clinical_trial_phase = EXCLUDED.clinical_trial_phase,
                target_sample_size = EXCLUDED.target_sample_size,
                estimated_completion_date = EXCLUDED.estimated_completion_date,
                primary_outcome = EXCLUDED.primary_outcome,
                sponsor_type = EXCLUDED.sponsor_type,
                updated_at = CURRENT_TIMESTAMP
            """,
            [
                (
                    t.drug,
                    t.trial_name,
                    t.registry_id,
                    t.clinical_trial_phase,
                    t.target_sample_size,
                    t.estimated_completion_date,
                    t.primary_outcome,
                    t.sponsor_type,
                )
                for t in filtered
            ],
        )
    return len(filtered)
