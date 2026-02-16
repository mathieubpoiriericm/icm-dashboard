-- SYNCED COPY: Must match the source of truth at sql/setup.sql
-- To verify: diff sql/setup.sql helm/svd-dashboard/sql/setup.sql
-- To sync:   cp sql/setup.sql helm/svd-dashboard/sql/setup.sql

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
);

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
);

-- Track processed PubMed papers to avoid reprocessing
CREATE TABLE IF NOT EXISTS pubmed_refs (
    id SERIAL PRIMARY KEY,
    pmid VARCHAR(20) NOT NULL UNIQUE,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    fulltext_available BOOLEAN DEFAULT FALSE,
    source VARCHAR(50),
    genes_extracted INTEGER DEFAULT 0
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_genes_gene ON genes(gene);
CREATE INDEX IF NOT EXISTS idx_trials_registry ON clinical_trials(registry_id);
CREATE INDEX IF NOT EXISTS idx_trials_drug ON clinical_trials(drug);
CREATE INDEX IF NOT EXISTS idx_pubmed_refs_pmid ON pubmed_refs(pmid);

-- Update timestamp trigger function
CREATE OR REPLACE FUNCTION update_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Apply trigger to genes table
DROP TRIGGER IF EXISTS genes_updated ON genes;
CREATE TRIGGER genes_updated
    BEFORE UPDATE ON genes
    FOR EACH ROW EXECUTE FUNCTION update_timestamp();

-- Apply trigger to clinical_trials table
DROP TRIGGER IF EXISTS trials_updated ON clinical_trials;
CREATE TRIGGER trials_updated
    BEFORE UPDATE ON clinical_trials
    FOR EACH ROW EXECUTE FUNCTION update_timestamp();