import asyncpg
from contextlib import asynccontextmanager
from typing import Optional
import os

# Database credentials
DB_HOST = os.getenv("DB_HOST")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_PASSWORD = os.getenv("DB_PASSWORD")


class Database:
    _pool: Optional[asyncpg.Pool] = None

    @classmethod
    async def get_pool(cls) -> asyncpg.Pool:
        if cls._pool is None:
            cls._pool = await asyncpg.create_pool(
                host=DB_HOST,
                port=DB_PORT,
                user=DB_USER,
                password=DB_PASSWORD,
                database=DB_NAME,
                min_size=2,
                max_size=10,
            )
        return cls._pool

    @classmethod
    async def close(cls):
        if cls._pool:
            await cls._pool.close()
            cls._pool = None

    @classmethod
    @asynccontextmanager
    async def connection(cls):
        pool = await cls.get_pool()
        async with pool.acquire() as conn:
            yield conn


async def get_existing_genes() -> set:
    """Fetch all gene symbols currently in the database."""
    async with Database.connection() as conn:
        rows = await conn.fetch("SELECT UPPER(gene) as gene FROM genes")
        return {row["gene"] for row in rows}


async def get_existing_trials() -> set:
    """Fetch all registry IDs currently in the database."""
    async with Database.connection() as conn:
        rows = await conn.fetch(
            "SELECT registry_id FROM clinical_trials WHERE registry_id IS NOT NULL"
        )
        return {row["registry_id"] for row in rows}


async def get_existing_pmids() -> set:
    """Fetch all PMIDs already processed."""
    async with Database.connection() as conn:
        rows = await conn.fetch("SELECT pmid FROM pubmed_refs")
        return {row["pmid"] for row in rows}


async def insert_gene(gene_data: dict) -> bool:
    """Insert or update a gene entry."""
    async with Database.connection() as conn:
        await conn.execute(
            """
            INSERT INTO genes (
                protein, gene, chromosomal_location, gwas_trait,
                mendelian_randomization, evidence_from_other_omics_studies,
                link_to_monogenetic_disease, brain_cell_types,
                affected_pathway, "references"
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT (gene) DO UPDATE SET
                gwas_trait = EXCLUDED.gwas_trait,
                evidence_from_other_omics_studies = EXCLUDED.evidence_from_other_omics_studies,
                "references" = genes."references" || '; ' || EXCLUDED."references",
                updated_at = CURRENT_TIMESTAMP
        """,
            *[
                gene_data.get(k)
                for k in [
                    "protein",
                    "gene",
                    "chromosomal_location",
                    "gwas_trait",
                    "mendelian_randomization",
                    "evidence_from_other_omics_studies",
                    "link_to_monogenetic_disease",
                    "brain_cell_types",
                    "affected_pathway",
                    "references",
                ]
            ],
        )
    return True


async def insert_trial(trial_data: dict) -> bool:
    """Insert a new clinical trial entry."""
    async with Database.connection() as conn:
        await conn.execute(
            """
            INSERT INTO clinical_trials (
                drug, mechanism_of_action, genetic_target, genetic_evidence,
                trial_name, registry_id, clinical_trial_phase, svd_population,
                svd_population_details, target_sample_size,
                estimated_completion_date, primary_outcome, sponsor_type
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            ON CONFLICT (registry_id, drug) DO NOTHING
        """,
            *[
                trial_data.get(k)
                for k in [
                    "drug",
                    "mechanism_of_action",
                    "genetic_target",
                    "genetic_evidence",
                    "trial_name",
                    "registry_id",
                    "clinical_trial_phase",
                    "svd_population",
                    "svd_population_details",
                    "target_sample_size",
                    "estimated_completion_date",
                    "primary_outcome",
                    "sponsor_type",
                ]
            ],
        )
    return True


async def record_processed_pmid(
    pmid: str,
    fulltext_available: bool = False,
    source: str = "abstract",
    genes_extracted: int = 0,
    trials_extracted: int = 0,
) -> bool:
    """Record a processed PMID to avoid reprocessing."""
    async with Database.connection() as conn:
        await conn.execute(
            """
            INSERT INTO pubmed_refs (
                pmid, fulltext_available, source,
                genes_extracted, trials_extracted
            ) VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (pmid) DO UPDATE SET
                fulltext_available = EXCLUDED.fulltext_available,
                source = EXCLUDED.source,
                genes_extracted = EXCLUDED.genes_extracted,
                trials_extracted = EXCLUDED.trials_extracted,
                processed_at = CURRENT_TIMESTAMP
        """,
            pmid,
            fulltext_available,
            source,
            genes_extracted,
            trials_extracted,
        )
    return True
