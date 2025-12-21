# constants.R
# Application-wide constants to avoid magic numbers and improve maintainability
# nolint start: object_name_linter.

# =============================================================================
# DATA COLUMN INDICES
# =============================================================================
# Table 2 column positions (used in data_prep.R)
#' @description Column index for Trial Name in table2
TABLE2_TRIAL_NAME_COL <- 5L
#' @description Column index for Primary Outcome in table2
TABLE2_PRIMARY_OUTCOME_COL <- 12L

# =============================================================================
# SAMPLE SIZE FILTER BOUNDS
# =============================================================================
# These values represent the min/max target sample sizes in clinical trials
#' @description Minimum target sample size in clinical trials dataset
SAMPLE_SIZE_MIN <- 15L
#' @description Maximum target sample size in clinical trials dataset
SAMPLE_SIZE_MAX <- 3156L

# =============================================================================
# HISTOGRAM CONFIGURATION
# =============================================================================
#' @description Number of histogram breaks for sample size distribution
HISTOGRAM_BREAKS <- 15L
#' @description X-axis maximum for sample size histogram
HISTOGRAM_XLIM_MAX <- 3500L
#' @description X-axis tick interval for sample size histogram
HISTOGRAM_TICK_INTERVAL <- 500L

# =============================================================================
# DEBOUNCE DELAYS (milliseconds)
# =============================================================================
#' @description Debounce delay for slider input (ms)
SLIDER_DEBOUNCE_MS <- 500L
#' @description Debounce delay for checkbox filters (ms)
CHECKBOX_DEBOUNCE_MS <- 150L
#' @description DataTable search delay (ms)
DATATABLE_SEARCH_DELAY <- 500L

# =============================================================================
# DATATABLE CONFIGURATION
# =============================================================================
#' @description Default page length for DataTables
DATATABLE_PAGE_LENGTH <- 10L

#' @description Server-side processing for DataTables
#' Set to TRUE for large datasets (>1000 rows) to improve initial load time
#' Set to FALSE for smaller datasets for better client-side search/filter
DATATABLE_SERVER_SIDE <- FALSE

# =============================================================================
# ASYNC LOADING CONFIGURATION
# =============================================================================
#' @description Enable async loading for Table 2 data
#' Requires: promises, future packages
#' Set to TRUE for non-blocking UI during data load (recommended for slow I/O)
ASYNC_TABLE2_LOADING <- FALSE

# =============================================================================
# CACHE CONFIGURATION
# =============================================================================
#' @description Maximum size for in-memory tooltip cache (bytes)
MEMO_CACHE_SIZE <- 50 * 1024^2 # 50MB

# =============================================================================
# CLINICAL TRIAL REGISTRIES
# =============================================================================
#' @description Supported clinical trial registry ID patterns
REGISTRY_PATTERNS <- c("NCT", "ISRCTN", "ACTRN", "ChiCTR")

#' @description Registry URL templates
REGISTRY_URLS <- list(
  NCT = "https://clinicaltrials.gov/study/",
  ISRCTN = "https://www.isrctn.com/",
  ChiCTR = "https://www.chictr.org.cn/showproj.html?proj=",
  ACTRN = "https://www.anzctr.org.au/Trial/Registration/TrialReview.aspx?id="
)

# =============================================================================
# EXTERNAL URLS
# =============================================================================
#' @description Base URL for NCBI Gene pages
NCBI_GENE_BASE_URL <- "https://www.ncbi.nlm.nih.gov/gene/"
#' @description Base URL for PubMed pages
PUBMED_BASE_URL <- "https://pubmed.ncbi.nlm.nih.gov/"

# =============================================================================
# DATA FILE PATHS
# =============================================================================
#' @description Paths to RDS files
DATA_PATHS <- list(
  table1_clean = "data/rdata/table1_clean.rds",
  table2_clean = "data/rdata/table2_clean.rds",
  gene_info = "data/rdata/gene_info_results_df.rds",
  gene_info_table2 = "data/rdata/gene_info_table2.rds",
  prot_info = "data/rdata/prot_info_clean.rds",
  refs = "data/rdata/refs.rds",
  gwas_trait_names = "data/rdata/gwas_trait_names.rds",
  omim_info = "data/csv/omim_info.csv"
)

# =============================================================================
# DISPLAY CONSTANTS
# =============================================================================
#' @description Placeholder text for missing data
PLACEHOLDER_NONE_FOUND <- "(none found)"
PLACEHOLDER_UNKNOWN <- "(unknown)"
PLACEHOLDER_REFERENCE_NEEDED <- "(reference needed)"

#' @description Special completion date text
COMPLETION_UNPUBLISHED <- "Completed (unpublished)"

# =============================================================================
# OMICS TYPE FULL NAMES
# =============================================================================
#' @description Mapping of omics type abbreviations to full names
OMICS_FULL_NAMES <- c(
  PWAS = "Proteome-Wide Association Study",
  EWAS = "Epigenome-Wide Association Study",
  TWAS = "Transcriptome-Wide Association Study"
)

# =============================================================================
# BRAIN CELL TYPE MAPPINGS
# =============================================================================
#' @description Mapping of brain cell type abbreviations to full names
CELL_TYPE_MAP <- list(
  EC = "Endothelial Cells",
  SMC = "Smooth Muscle Cells",
  VSMC = "Vascular Smooth Muscle Cells",
  AC = "Astrocytes",
  MG = "Microglia",
  OL = "Oligodendrocytes",
  PC = "Pericytes",
  FB = "Fibroblasts"
)

# =============================================================================
# GWAS TRAITS REQUIRING TITLE CASE
# =============================================================================
#' @description GWAS traits that should be displayed in title case
GWAS_TRAITS_TITLE_CASE <- c(
  "extreme-cSVD",
  "lacunes",
  "stroke",
  "lacunar stroke"
)
# nolint end: object_name_linter.
