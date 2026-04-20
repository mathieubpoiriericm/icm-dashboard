-- Migration: Add tables for external data cache
-- These tables store data fetched from NCBI, UniProt, and PubMed APIs
-- to allow trigger_update.R to read from database instead of making API calls

-- NCBI Gene information cache
CREATE TABLE IF NOT EXISTS ncbi_gene_info (
    id SERIAL PRIMARY KEY,
    gene_symbol VARCHAR(100) NOT NULL UNIQUE,
    ncbi_uid VARCHAR(20),
    description TEXT,
    aliases TEXT,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- UniProt protein information cache
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
);

-- PubMed citation cache
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
);

-- Indexes for efficient lookups
CREATE INDEX IF NOT EXISTS idx_ncbi_gene_symbol ON ncbi_gene_info(gene_symbol);
CREATE INDEX IF NOT EXISTS idx_uniprot_gene_symbol ON uniprot_info(gene_symbol);
CREATE INDEX IF NOT EXISTS idx_pubmed_citations_pmid ON pubmed_citations(pmid);

-- Apply update timestamp triggers
DROP TRIGGER IF EXISTS ncbi_gene_info_updated ON ncbi_gene_info;
CREATE TRIGGER ncbi_gene_info_updated
    BEFORE UPDATE ON ncbi_gene_info
    FOR EACH ROW EXECUTE FUNCTION update_timestamp();

DROP TRIGGER IF EXISTS uniprot_info_updated ON uniprot_info;
CREATE TRIGGER uniprot_info_updated
    BEFORE UPDATE ON uniprot_info
    FOR EACH ROW EXECUTE FUNCTION update_timestamp();

DROP TRIGGER IF EXISTS pubmed_citations_updated ON pubmed_citations;
CREATE TRIGGER pubmed_citations_updated
    BEFORE UPDATE ON pubmed_citations
    FOR EACH ROW EXECUTE FUNCTION update_timestamp();
