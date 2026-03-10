# trigger_update.R
# Reads from database and generates QS files for the Shiny dashboard
#
# This script is called after the Python pipeline updates the database.
# It reads cleaned data and external data cache from the database,
# then generates QS files for fast loading by the Shiny app.
#
# Prerequisites:
# 1. Run: python pipeline/main.py (to populate genes table)
# 2. Run: python pipeline/main.py --sync-external-data
#    (to populate cache tables)
# 3. Run: Rscript scripts/trigger_update.R (this script)

# =============================================================================
# SETUP
# =============================================================================

# Set working directory to project root if running from scripts/
if (grepl("scripts$", getwd())) {
  setwd("..")
}

message("Starting dashboard data update...")
message(sprintf("Working directory: %s", getwd()))

# Source required utility functions
source("R/utils.R")
source("R/clean_table1.R")
source("R/clean_table2.R")
source("R/read_external_data.R")

# Ensure data directories exist
if (!dir.exists("data/qs")) {
  dir.create("data/qs", recursive = TRUE)
}
if (!dir.exists("data/txt")) {
  dir.create("data/txt", recursive = TRUE)
}

# =============================================================================
# STEP 1: Clean and save Table 1 (genes) from database
# =============================================================================
message("\n[1/7] Fetching and cleaning Table 1 (genes) from database...")
table1_clean <- clean_table1()
qs::qsave(table1_clean, "data/qs/table1_clean.qs")
message("Saved: data/qs/table1_clean.qs")

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
qs::qsave(table2_clean, "data/qs/table2_clean.qs")
message("Saved: data/qs/table2_clean.qs")

# =============================================================================
# STEP 3: Read NCBI gene info for Table 1 genes from database cache
# =============================================================================
message("\n[3/7] Reading NCBI gene info for Table 1 genes from database...")
gene_info_results_df <- read_ncbi_gene_info_from_db(gene_symbols_table1)
qs::qsave(gene_info_results_df, "data/qs/gene_info_results_df.qs")
message(sprintf(
  "Saved: data/qs/gene_info_results_df.qs (%d genes)",
  nrow(gene_info_results_df)
))

# =============================================================================
# STEP 4: Read NCBI gene info for Table 2 genes from database cache
# =============================================================================
message("\n[4/7] Reading NCBI gene info for Table 2 genes from database...")
gene_info_table2 <- read_table2_ncbi_info_db()
qs::qsave(gene_info_table2, "data/qs/gene_info_table2.qs")
message(sprintf(
  "Saved: data/qs/gene_info_table2.qs (%d genes)",
  nrow(gene_info_table2)
))

# =============================================================================
# STEP 5: Read UniProt protein info from database cache
# =============================================================================
message("\n[5/7] Reading UniProt protein info from database...")
prot_info_clean <- read_uniprot_data_from_db(gene_symbols_table1)
qs::qsave(prot_info_clean, "data/qs/prot_info_clean.qs")
message(sprintf(
  "Saved: data/qs/prot_info_clean.qs (%d proteins)",
  nrow(prot_info_clean)
))

# =============================================================================
# STEP 6: Read PubMed references from database cache
# =============================================================================
message("\n[6/7] Reading PubMed references from database...")

# Extract unique PMIDs from table1 References column
# Helper function to extract PMIDs from text
extract_unique_pmids <- function(references_column) {
  valid <- !is.na(references_column) & nchar(references_column) > 0L
  all_pmids <- unlist(
    regmatches(references_column[valid], gregexpr("\\b\\d{7,8}\\b", references_column[valid])),
    use.names = FALSE
  )
  unique(all_pmids)
}

pmids <- extract_unique_pmids(table1_clean$References)
message(sprintf("Found %d unique PMIDs", length(pmids)))

if (length(pmids) > 0) {
  refs <- read_pubmed_refs_from_db(pmids)
  qs::qsave(refs, "data/qs/refs.qs")
  message(sprintf("Saved: data/qs/refs.qs (%d references)", nrow(refs)))
} else {
  # Save empty data frame
  refs <- data.frame(
    pmid = character(0),
    formatted_ref = character(0),
    stringsAsFactors = FALSE
  )
  qs::qsave(refs, "data/qs/refs.qs")
  message("No PMIDs found, saved empty refs.qs")
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
qs::qsave(gwas_trait_names, "data/qs/gwas_trait_names.qs")
message(sprintf(
  "Saved: data/qs/gwas_trait_names.qs (%d traits)",
  nrow(gwas_trait_names)
))

# =============================================================================
# SUMMARY
# =============================================================================
message("\n========================================")
message("Dashboard data update completed successfully!")
message("========================================")
message("\nGenerated QS files:")
message("  - data/qs/table1_clean.qs")
message("  - data/qs/table2_clean.qs")
message("  - data/qs/gene_info_results_df.qs")
message("  - data/qs/gene_info_table2.qs")
message("  - data/qs/prot_info_clean.qs")
message("  - data/qs/refs.qs")
message("  - data/qs/gwas_trait_names.qs")
message("\nNote: External data (NCBI, UniProt, PubMed) is read from DB cache.")
message("Run 'python pipeline/main.py --sync-external-data' to refresh cache.")
