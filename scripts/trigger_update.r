# trigger_update.R
# Called after Python pipeline updates the database
# Regenerates all RDS objects used by the Shiny dashboard

# =============================================================================
# SETUP
# =============================================================================

# Set working directory to project root if running from scripts/
if (grepl("scripts$", getwd())) {
  setwd("..")
}

message("Starting dashboard data update...")
message(sprintf("Working directory: %s", getwd()))

# Source required utility functions (clean_table functions depend on these)
source("R/utils.R")
source("R/clean_table1.R")
source("R/clean_table2.R")
source("R/fetch_ncbi_gene_data.R")
source("R/fetch_uniprot_data.R")
source("R/fetch_pubmed_data.R")

# Ensure data directories exist
if (!dir.exists("data/rdata")) {
  dir.create("data/rdata", recursive = TRUE)
}
if (!dir.exists("data/txt")) {
  dir.create("data/txt", recursive = TRUE)
}

# =============================================================================
# STEP 1: Clean and save Table 1 (genes) from database
# =============================================================================
message("\n[1/7] Fetching and cleaning Table 1 (genes) from database...")
table1_clean <- clean_table1()
saveRDS(table1_clean, file = "data/rdata/table1_clean.rds")
message("Saved: data/rdata/table1_clean.rds")

# Extract gene symbols from table1 for external data fetching
gene_symbols_table1 <- unique(table1_clean$Gene)
message(sprintf(
  "Found %d unique genes in Table 1",
  length(gene_symbols_table1)
))

# =============================================================================
# STEP 2: Clean and save Table 2 (clinical trials) from database
# =============================================================================
message("\n[2/7] Fetching and cleaning Table 2 (clinical trials)...")
table2_clean <- clean_table2()
saveRDS(table2_clean, file = "data/rdata/table2_clean.rds")
message("Saved: data/rdata/table2_clean.rds")

# =============================================================================
# STEP 3: Fetch and save NCBI gene info for Table 1 genes
# =============================================================================
message("\n[3/7] Fetching NCBI gene info for Table 1 genes...")
gene_info_results_df <- fetch_all_gene_info(
  gene_symbols_table1,
  delay = 0.1,
  verbose = TRUE
)
# Add gene symbol as first column for matching
gene_info_results_df <- cbind(
  name = gene_symbols_table1,
  gene_info_results_df
)
saveRDS(gene_info_results_df, file = "data/rdata/gene_info_results_df.rds")
message("Saved: data/rdata/gene_info_results_df.rds")

# =============================================================================
# STEP 4: Fetch and save NCBI gene info for Table 2 genes
# =============================================================================
message("\n[4/7] Fetching NCBI gene info for Table 2 genes...")
fetch_save_table2_gene_info(delay = 0.1, verbose = TRUE, extract_genes = TRUE)
message("Saved: data/rdata/gene_info_table2.rds")

# =============================================================================
# STEP 5: Fetch and save UniProt protein info
# =============================================================================
message("\n[5/7] Fetching UniProt protein info...")
prot_info_clean <- fetch_uniprot_data(
  gene_symbols_table1,
  organism = "9606",
  verbose = TRUE
)
saveRDS(prot_info_clean, file = "data/rdata/prot_info_clean.rds")
message("Saved: data/rdata/prot_info_clean.rds")

# =============================================================================
# STEP 6: Fetch and save PubMed references
# =============================================================================
message("\n[6/7] Fetching PubMed references...")
# Extract unique PMIDs from table1 References column
pmids <- extract_unique_pmids(table1_clean$References)
message(sprintf("Found %d unique PMIDs to fetch", length(pmids)))

if (length(pmids) > 0) {
  refs <- fetch_all_pubmed_refs(
    pmids,
    bibentry_dir = "bibentry",
    delay = 0.5, # 0.5 second delay between requests to avoid NCBI rate limiting
    verbose = TRUE
  )
  saveRDS(refs, file = "data/rdata/refs.rds")
  message("Saved: data/rdata/refs.rds")
} else {
  message("No PMIDs found, skipping PubMed fetch")
}

# =============================================================================
# STEP 7: Extract and save GWAS trait names mapping
# =============================================================================
message("\n[7/7] Extracting GWAS trait names...")
# Extract unique GWAS traits from table1
all_gwas_traits <- unique(unlist(table1_clean$`GWAS Trait`))
all_gwas_traits <- all_gwas_traits[
  !is.na(all_gwas_traits) & all_gwas_traits != "(none found)"
]

# Create a simple mapping data frame (abbreviation to full name)
# Note: Full names should be populated from an external source or manually
gwas_trait_names <- data.frame(
  abbrev = all_gwas_traits,
  full_name = all_gwas_traits, # Default to same as abbrev if no mapping exists
  stringsAsFactors = FALSE
)
saveRDS(gwas_trait_names, file = "data/rdata/gwas_trait_names.rds")
message("Saved: data/rdata/gwas_trait_names.rds")

# =============================================================================
# SUMMARY
# =============================================================================
message("\n========================================")
message("Dashboard data update completed successfully!")
message("========================================")
message("\nGenerated RDS files:")
message("  - data/rdata/table1_clean.rds")
message("  - data/rdata/table2_clean.rds")
message("  - data/rdata/gene_info_results_df.rds")
message("  - data/rdata/gene_info_table2.rds")
message("  - data/rdata/prot_info_clean.rds")
message("  - data/rdata/refs.rds")
message("  - data/rdata/gwas_trait_names.rds")
message("\nNote: OMIM data (data/csv/omim_info.csv) is managed separately.")
