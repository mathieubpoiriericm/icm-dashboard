# constants.R
# Application-wide constants to avoid magic numbers and improve maintainability
# nolint start: object_name_linter.

# =============================================================================
# DATA COLUMN INDICES
# =============================================================================
# Table 2 column positions (used in data_prep.R)
# Column index for Trial Name in table2
TABLE2_TRIAL_NAME_COL <- 5L
# Column index for Primary Outcome in table2
TABLE2_PRIMARY_OUTCOME_COL <- 12L

# =============================================================================
# SAMPLE SIZE FILTER BOUNDS
# =============================================================================
# These values represent the min/max target sample sizes in clinical trials
# Minimum target sample size in clinical trials dataset
SAMPLE_SIZE_MIN <- 15L
# Maximum target sample size in clinical trials dataset
SAMPLE_SIZE_MAX <- 3156L

# =============================================================================
# HISTOGRAM CONFIGURATION
# =============================================================================
# Number of histogram breaks for sample size distribution
HISTOGRAM_BREAKS <- 15L
# X-axis maximum for sample size histogram
HISTOGRAM_XLIM_MAX <- 3500L
# X-axis tick interval for sample size histogram
HISTOGRAM_TICK_INTERVAL <- 500L

# =============================================================================
# DEBOUNCE DELAYS (milliseconds)
# =============================================================================
# Debounce delay for slider input (ms)
SLIDER_DEBOUNCE_MS <- 500L
# Debounce delay for checkbox filters (ms)
CHECKBOX_DEBOUNCE_MS <- 150L
# DataTable search delay (ms)
DATATABLE_SEARCH_DELAY <- 500L

# =============================================================================
# DATATABLE CONFIGURATION
# =============================================================================
# Default page length for DataTables
DATATABLE_PAGE_LENGTH <- 10L

# Server-side processing for DataTables
# Set to TRUE for large datasets (>1000 rows) to improve initial load time
# Set to FALSE for smaller datasets for better client-side search/filter
DATATABLE_SERVER_SIDE <- FALSE

# =============================================================================
# PRELOAD CONFIGURATION
# =============================================================================
# Preload Table 2 data at app startup
# This eliminates the delay when first accessing Clinical Trials tabs
# Can be disabled via environment variable for memory-constrained environments:
#   PRELOAD_TABLE2=FALSE docker run ...
PRELOAD_TABLE2 <- as.logical(Sys.getenv("PRELOAD_TABLE2", unset = "TRUE"))

# =============================================================================
# CACHE CONFIGURATION
# =============================================================================
# Maximum size for in-memory tooltip cache (bytes)
MEMO_CACHE_SIZE <- 50 * 1024^2 # 50MB

# =============================================================================
# CLINICAL TRIAL REGISTRIES
# =============================================================================
# Supported clinical trial registry ID patterns
REGISTRY_PATTERNS <- c("NCT", "ISRCTN", "ACTRN", "ChiCTR")

# Registry URL templates
REGISTRY_URLS <- list(
  NCT = "https://clinicaltrials.gov/study/",
  ISRCTN = "https://www.isrctn.com/",
  ChiCTR = "https://www.chictr.org.cn/showproj.html?proj=",
  ACTRN = "https://www.anzctr.org.au/Trial/Registration/TrialReview.aspx?id="
)

# Registry display names for tooltip buttons
REGISTRY_LABELS <- list(
  NCT = "ClinicalTrials.gov",
  ISRCTN = "ISRCTN",
  ChiCTR = "ChiCTR",
  ACTRN = "ANZCTR"
)

# =============================================================================
# EXTERNAL URLS
# =============================================================================
# Base URL for NCBI Gene pages
NCBI_GENE_BASE_URL <- "https://www.ncbi.nlm.nih.gov/gene/"
# Base URL for PubMed pages
PUBMED_BASE_URL <- "https://pubmed.ncbi.nlm.nih.gov/"

# =============================================================================
# DATA FILE PATHS
# =============================================================================
# Paths to data files (qs format by default, falls back to rds)
DATA_PATHS <- list(
  table1_clean = "data/qs/table1_clean.qs",
  table2_clean = "data/qs/table2_clean.qs",
  gene_info = "data/qs/gene_info_results_df.qs",
  gene_info_table2 = "data/qs/gene_info_table2.qs",
  prot_info = "data/qs/prot_info_clean.qs",
  refs = "data/qs/refs.qs",
  gwas_trait_names = "data/qs/gwas_trait_names.qs",
  omim_info = "data/csv/omim_info.csv"
)

# =============================================================================
# BRAND COLORS (ICM Visual Identity)
# =============================================================================
# ICM navy — primary brand color (maps to CSS --svd-primary)
BRAND_COLOR_PRIMARY <- "#281E78"
# ICM orange — accent brand color (maps to CSS --svd-accent)
BRAND_COLOR_ACCENT <- "#FA4616"
# Danger red (maps to CSS --svd-danger)
BRAND_COLOR_DANGER <- "#DC3545"

# =============================================================================
# CSS CLASS CONSTANTS
# =============================================================================
# CSS class name for elements with tooltips (styled in www/custom.css)
TOOLTIP_CLASS <- "tooltip-box"
# CSS class name for italic elements with tooltips (e.g., gene symbols)
TOOLTIP_CLASS_ITALIC <- "tooltip-box tooltip-box-italic"
# CSS class name for displaying active filter status
FILTER_ACTIVE_CLASS <- "filter-message filter-active"
# CSS class name for displaying when no filters are active
FILTER_NONE_CLASS <- "filter-message filter-none"

# =============================================================================
# DISPLAY CONSTANTS
# =============================================================================
# Placeholder text for missing data
PLACEHOLDER_NONE_FOUND <- "(none found)"
PLACEHOLDER_UNKNOWN <- "(unknown)"
PLACEHOLDER_REFERENCE_NEEDED <- "(reference needed)"

# Special completion date text
COMPLETION_UNPUBLISHED <- "Completed (unpublished)"

# =============================================================================
# OMICS TYPE FULL NAMES
# =============================================================================
# Mapping of omics type abbreviations to full names
OMICS_FULL_NAMES <- c(
  PWAS = "Proteome-Wide Association Study",
  EWAS = "Epigenome-Wide Association Study",
  TWAS = "Transcriptome-Wide Association Study"
)

# =============================================================================
# BRAIN CELL TYPE MAPPINGS
# =============================================================================
# Mapping of brain cell type abbreviations to full names
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
# GWAS traits that should be displayed in title case
GWAS_TRAITS_TITLE_CASE <- c(
  "extreme-cSVD",
  "lacunes",
  "stroke",
  "lacunar stroke"
)
# =============================================================================
# CLINICAL TRIALS MAP CONFIGURATION
# =============================================================================
# Geocoded trials cache file path
MAP_CACHE_PATH <- "data/qs/geocoded_trials.qs"

# ClinicalTrials.gov API v2 base URL
MAP_CT_API_BASE_URL <- "https://clinicaltrials.gov/api/v2/studies/"

# API request delay (milliseconds) to avoid rate limiting
MAP_API_DELAY_MS <- 100L

# Map default view (centered on Atlantic to show both Americas and Europe)
MAP_DEFAULT_LAT <- 30
MAP_DEFAULT_LNG <- 0
MAP_DEFAULT_ZOOM <- 2

# Map container height in pixels
MAP_HEIGHT_PX <- 700L
# =============================================================================
# PARALLELISM CONFIGURATION
# =============================================================================
# Number of threads for qs serialization (detected at source time)
.N_THREADS <- parallel::detectCores()

# =============================================================================
# SHARED DATATABLE JS SNIPPETS
# =============================================================================
# Center-align all header cells (used in initComplete callback)
DATATABLE_INIT_HEADER_JS <- "$(this.api().table().header()).find('th').css('text-align', 'center');"

# Initialize Tippy.js tooltips (used in drawCallback)
DATATABLE_TIPPY_CALLBACK_JS <- "if (typeof initializeTippy === 'function') { initializeTippy(); }"

# nolint end: object_name_linter.
