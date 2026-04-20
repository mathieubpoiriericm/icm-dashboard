"""Baseline schema — matches sql/setup.sql + sql/add_external_data_tables.sql.

This is the initial migration that captures the existing database schema.
Running 'alembic upgrade head' on a fresh database produces the same schema
as running the two SQL files manually.

Revision ID: 001
Revises: None
Create Date: 2025-02-12
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Core tables (from sql/setup.sql) ---

    op.execute("""
        CREATE TABLE IF NOT EXISTS genes (
            id SERIAL PRIMARY KEY,
            protein VARCHAR(255),
            gene VARCHAR(100) NOT NULL UNIQUE,
            chromosomal_location VARCHAR(50),
            gwas_trait TEXT,
            mendelian_randomization VARCHAR(10),
            evidence_from_other_omics_studies TEXT,
            link_to_monogenetic_disease TEXT,
            brain_cell_types VARCHAR(255),
            affected_pathway TEXT,
            "references" TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS clinical_trials (
            id SERIAL PRIMARY KEY,
            drug VARCHAR(255) NOT NULL,
            mechanism_of_action TEXT,
            genetic_target VARCHAR(255),
            genetic_evidence VARCHAR(10),
            trial_name TEXT,
            registry_id VARCHAR(50),
            clinical_trial_phase VARCHAR(20),
            svd_population VARCHAR(100),
            svd_population_details TEXT,
            target_sample_size INTEGER,
            estimated_completion_date VARCHAR(20),
            primary_outcome TEXT,
            sponsor_type VARCHAR(100),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(registry_id, drug)
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS pubmed_refs (
            id SERIAL PRIMARY KEY,
            pmid VARCHAR(20) NOT NULL UNIQUE,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            fulltext_available BOOLEAN DEFAULT FALSE,
            source VARCHAR(50),
            genes_extracted INTEGER DEFAULT 0
        )
    """)

    # Indexes for core tables
    op.execute("CREATE INDEX IF NOT EXISTS idx_genes_gene ON genes(gene)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_trials_registry ON clinical_trials(registry_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_trials_drug ON clinical_trials(drug)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_pubmed_refs_pmid ON pubmed_refs(pmid)")

    # Update timestamp trigger function
    op.execute("""
        CREATE OR REPLACE FUNCTION update_timestamp()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = CURRENT_TIMESTAMP;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)

    # Apply trigger to genes table
    op.execute("DROP TRIGGER IF EXISTS genes_updated ON genes")
    op.execute("""
        CREATE TRIGGER genes_updated
            BEFORE UPDATE ON genes
            FOR EACH ROW EXECUTE FUNCTION update_timestamp()
    """)

    # Apply trigger to clinical_trials table
    op.execute("DROP TRIGGER IF EXISTS trials_updated ON clinical_trials")
    op.execute("""
        CREATE TRIGGER trials_updated
            BEFORE UPDATE ON clinical_trials
            FOR EACH ROW EXECUTE FUNCTION update_timestamp()
    """)

    # --- External data cache tables (from sql/add_external_data_tables.sql) ---

    op.execute("""
        CREATE TABLE IF NOT EXISTS ncbi_gene_info (
            id SERIAL PRIMARY KEY,
            gene_symbol VARCHAR(100) NOT NULL UNIQUE,
            ncbi_uid VARCHAR(20),
            description TEXT,
            aliases TEXT,
            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS uniprot_info (
            id SERIAL PRIMARY KEY,
            gene_symbol VARCHAR(100) NOT NULL UNIQUE,
            accession VARCHAR(20),
            protein_name TEXT,
            biological_process TEXT,
            molecular_function TEXT,
            cellular_component TEXT,
            url TEXT,
            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS pubmed_citations (
            id SERIAL PRIMARY KEY,
            pmid VARCHAR(20) NOT NULL UNIQUE,
            authors TEXT,
            title TEXT,
            journal TEXT,
            publication_date VARCHAR(50),
            doi VARCHAR(100),
            formatted_ref TEXT,
            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Indexes for external data tables
    op.execute("CREATE INDEX IF NOT EXISTS idx_ncbi_gene_symbol ON ncbi_gene_info(gene_symbol)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_uniprot_gene_symbol ON uniprot_info(gene_symbol)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_pubmed_citations_pmid ON pubmed_citations(pmid)")

    # Apply update timestamp triggers to external data tables
    op.execute("DROP TRIGGER IF EXISTS ncbi_gene_info_updated ON ncbi_gene_info")
    op.execute("""
        CREATE TRIGGER ncbi_gene_info_updated
            BEFORE UPDATE ON ncbi_gene_info
            FOR EACH ROW EXECUTE FUNCTION update_timestamp()
    """)

    op.execute("DROP TRIGGER IF EXISTS uniprot_info_updated ON uniprot_info")
    op.execute("""
        CREATE TRIGGER uniprot_info_updated
            BEFORE UPDATE ON uniprot_info
            FOR EACH ROW EXECUTE FUNCTION update_timestamp()
    """)

    op.execute("DROP TRIGGER IF EXISTS pubmed_citations_updated ON pubmed_citations")
    op.execute("""
        CREATE TRIGGER pubmed_citations_updated
            BEFORE UPDATE ON pubmed_citations
            FOR EACH ROW EXECUTE FUNCTION update_timestamp()
    """)


def downgrade() -> None:
    # Drop triggers first
    op.execute("DROP TRIGGER IF EXISTS pubmed_citations_updated ON pubmed_citations")
    op.execute("DROP TRIGGER IF EXISTS uniprot_info_updated ON uniprot_info")
    op.execute("DROP TRIGGER IF EXISTS ncbi_gene_info_updated ON ncbi_gene_info")
    op.execute("DROP TRIGGER IF EXISTS trials_updated ON clinical_trials")
    op.execute("DROP TRIGGER IF EXISTS genes_updated ON genes")

    # Drop trigger function
    op.execute("DROP FUNCTION IF EXISTS update_timestamp()")

    # Drop tables (reverse order to respect potential FK constraints)
    op.execute("DROP TABLE IF EXISTS pubmed_citations")
    op.execute("DROP TABLE IF EXISTS uniprot_info")
    op.execute("DROP TABLE IF EXISTS ncbi_gene_info")
    op.execute("DROP TABLE IF EXISTS pubmed_refs")
    op.execute("DROP TABLE IF EXISTS clinical_trials")
    op.execute("DROP TABLE IF EXISTS genes")
