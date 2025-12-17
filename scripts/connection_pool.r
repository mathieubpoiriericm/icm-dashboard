# PLANNED: Connection pool for efficient database access
get_db_pool <- function() {
    pool::dbPool(
        drv = RPostgres::Postgres(),
        dbname = Sys.getenv("DB_NAME", "csvd_dashboard"),
        host = Sys.getenv("DB_HOST", "localhost"),
        port = as.integer(Sys.getenv("DB_PORT", "5432")),
        user = Sys.getenv("PGUSER"),
        password = Sys.getenv("PGPASSWORD"),
        minSize = 1,
        maxSize = 5
    )
}

# PLANNED: Fetch Table 1 (genes) from database
fetch_table1_from_db <- function(pool) {
    DBI::dbGetQuery(
        pool,
        "
    SELECT protein, gene, chromosomal_location,
           gwas_trait AS GWAS_trait,
           mendelian_randomization,
           evidence_from_other_omics_studies,
           link_to_monogenetic_disease,
           brain_cell_types, affected_pathway, references
    FROM genes
    ORDER BY gene
  "
    )
}

# PLANNED: Fetch Table 2 (clinical trials) from database
fetch_table2_from_db <- function(pool) {
    DBI::dbGetQuery(
        pool,
        "
    SELECT drug, mechanism_of_action, genetic_target,
           genetic_evidence, trial_name, registry_id AS registry_ID,
           clinical_trial_phase, svd_population, svd_population_details,
           target_sample_size, estimated_completion_date,
           primary_outcome, sponsor_type
    FROM clinical_trials
    ORDER BY drug
  "
    )
}

# PLANNED: Fetch OMIM info from database
fetch_omim_from_db <- function(pool) {
    DBI::dbGetQuery(pool, "SELECT omim_number, phenotype FROM omim_info")
}
