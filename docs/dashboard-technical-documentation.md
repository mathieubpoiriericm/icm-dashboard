# Dashboard Technical Documentation

> Volume I of the Cerebral SVD Dashboard Complete Technical Documentation

---

## Table of Contents

- [Chapter 1: Introduction](#chapter-1-introduction)
- [Chapter 2: Project Structure](#chapter-2-project-structure)
  - [Directory Overview](#directory-overview)
  - [File Responsibilities](#file-responsibilities)
- [Chapter 3: Application Entry Point: app.R](#chapter-3-application-entry-point-appr)
  - [Startup Sequence](#startup-sequence)
  - [Sass Cache Configuration](#sass-cache-configuration)
  - [CSS and JavaScript Minification](#css-and-javascript-minification)
- [Chapter 4: Data Layer](#chapter-4-data-layer)
  - [Data Files](#data-files)
  - [QS Fast Serialization](#qs-fast-serialization)
  - [data_prep.R Functions](#data_prepr-functions)
  - [Preloading Strategy](#preloading-strategy)
- [Chapter 5: Application Constants (constants.R)](#chapter-5-application-constants-constantsr)
- [Chapter 6: Tooltip System](#chapter-6-tooltip-system)
  - [Overview](#overview)
  - [Memoisation](#memoisation)
  - [Key Functions in tooltips.R](#key-functions-in-tooltipsr)
- [Chapter 7: Shiny Module: Checkbox Filters](#chapter-7-shiny-module-checkbox-filters)
  - [Module Pattern](#module-pattern)
  - [checkbox_filter_ui()](#checkbox_filter_ui)
  - [checkbox_filter_server()](#checkbox_filter_server)
- [Chapter 8: User Interface (ui.R)](#chapter-8-user-interface-uir)
  - [Structure](#structure)
  - [Tab Structure](#tab-structure)
  - [Gene Table Filters](#gene-table-filters)
  - [Clinical Trials Table Filters](#clinical-trials-table-filters)
  - [bslib Bootstrap 5 Integration](#bslib-bootstrap-5-integration)
  - [Glassmorphism Design System](#glassmorphism-design-system)
- [Chapter 9: Server Logic (Modular Architecture)](#chapter-9-server-logic-modular-architecture)
  - [Main Server Orchestrator (server.R)](#main-server-orchestrator-serverr)
  - [Filtering Logic](#filtering-logic)
- [Chapter 10: Performance Optimizations](#chapter-10-performance-optimizations)
  - [Pre-computation](#pre-computation)
  - [Caching](#caching)
  - [Debouncing](#debouncing)
  - [Browser Performance Optimization](#browser-performance-optimization)
- [Chapter 11: Testing Infrastructure](#chapter-11-testing-infrastructure)
  - [Test Coverage](#test-coverage)
  - [Running Tests](#running-tests)
- [Chapter 12: Adding New Features](#chapter-12-adding-new-features)
  - [Adding a New Filter](#adding-a-new-filter)
  - [Adding a New Column with Tooltips](#adding-a-new-column-with-tooltips)
  - [Adding a New Tab](#adding-a-new-tab)
- [Chapter 13: Dependencies](#chapter-13-dependencies)
  - [R Packages](#r-packages)
- [Chapter 14: Troubleshooting](#chapter-14-troubleshooting)
  - [Common Issues](#common-issues)
  - [Debug Tips](#debug-tips)
- [Chapter 15: Clinical Trials Map Module](#chapter-15-clinical-trials-map-module)
  - [Overview](#map-overview)
  - [Data Flow](#data-flow)
  - [API Integration](#api-integration)
  - [Geocoding](#geocoding)
  - [Caching Strategy](#caching-strategy)
  - [Map Server Functions](#map-server-functions)
  - [Popup Content Generation](#popup-content-generation)

---

## Chapter 1: Introduction

The SVD Dashboard is an interactive R Shiny application that provides researchers with:

- A searchable table of putative causal genes for Cerebral Small Vessel Disease
- Clinical trial information for SVD-related drugs
- Interactive visualizations including a phenogram and clinical trial timeline
- Filtering capabilities based on GWAS traits, omics studies, and trial characteristics

The application follows a modular architecture with clear separation of concerns between data preparation, UI definition, and server logic.

---

## Chapter 2: Project Structure

### Directory Overview

```
rshiny_dashboard/                            Lines
|-- app.R                                      407
|-- R/
|   |-- fetch_trial_locations.R                865  # NEW: ClinicalTrials.gov API + geocoding
|   |-- data_prep.R                            826
|   |-- tooltips.R                             742
|   |-- ui.R                                   631
|   |-- server_table2.R                        610
|   |-- utils.R                                402
|   |-- filter_utils.R                         296
|   |-- server_table1.R                        291
|   |-- server.R                               275
|   |-- server_map.R                           266  # NEW: Map tab server logic
|   |-- read_external_data.R                   251  # Database cache reader
|   |-- clean_table1.R                         239
|   |-- constants.R                            169
|   |-- mod_checkbox_filter.R                  167
|   +-- clean_table2.R                          61
|-- data/
|   |-- csv/                     # CSV data files
|   |-- qs/                      # QS data files (fast serialization)
|   |-- txt/                     # Text data files
|   +-- xlsx/                    # Excel data files
|-- pipeline/                    # Automated data pipeline (Python)
|   |-- main.py                                274
|   |-- validation.py                          286
|   |-- pdf_retrieval.py                       191
|   |-- database.py                            167
|   |-- llm_extraction.py                      139
|   |-- data_merger.py                         113
|   |-- pubmed_search.py                        32
|   +-- quality_metrics.py                      20
|-- scripts/
|   |-- python_plot.py           # Clinical trials visualization generator
|   +-- trigger_update.R         # Regenerate QS from database
|-- tests/
|   +-- test_all.R               # Unit tests (20KB)
|-- www/
|   |-- custom.css                           2,484
|   |-- custom.min.css           # Minified CSS (generated at startup)
|   |-- custom.js                              372
|   |-- custom.min.js            # Minified JS (generated at startup)
|   |-- python_plot.js                         600
|   |-- python_plot.min.js       # Minified JS (generated at startup)
|   |-- phenogram_template.html                340
|   |-- python_plot.html                       268
|   |-- css/
|   |   +-- tippy.css                           43
|   |-- js/
|   |   |-- popper.min.js                        6
|   |   +-- tippy.min.js                         2
|   |-- fonts/                   # Roboto font
|   +-- images/                  # Logo and images (WebP + PNG)
                                 ---------------
                                 Total:    12,377
```

### File Responsibilities

| File | Responsibility |
|------|----------------|
| **Core Application Files** | |
| `app.R` | Entry point; loads packages, sources modules, initializes data, builds and runs the app |
| `R/server.R` | Main server orchestrator; composes Table 1 and Table 2 server modules |
| `R/server_table1.R` | Table 1 (Gene Table) server logic, filtering, and DataTable rendering |
| `R/server_table2.R` | Table 2 (Clinical Trials) server logic, preloading, filtering, and histogram |
| `R/ui.R` | Builds the complete UI using `bslib::page_navbar()` with Bootstrap 5 theming and dark mode toggle |
| `R/constants.R` | Application-wide constants: column indices, bounds, URLs, debounce delays |
| `R/data_prep.R` | Functions for loading and preprocessing Table 1 (genes) and Table 2 (clinical trials) data |
| `R/tooltips.R` | Functions that generate HTML tooltip markup for interactive table cells |
| `R/mod_checkbox_filter.R` | Shiny module for checkbox filter groups with "Show All" toggle behavior |
| `R/filter_utils.R` | Unified filter utilities for data.table filtering with `apply_column_filter()` and filter message rendering |
| `R/utils.R` | Reusable CSS style strings, database connection utilities, and column cleaning helpers |
| **Data Fetching/Cleaning Scripts** | |
| `R/clean_table1.R` | Cleans and preprocesses raw Table 1 (gene) data |
| `R/clean_table2.R` | Cleans and preprocesses raw Table 2 (clinical trial) data |
| **Map Module Files** | |
| `R/server_map.R` | Clinical Trials Map tab server logic with lazy loading and leafletProxy |
| `R/fetch_trial_locations.R` | ClinicalTrials.gov API v2 integration, geocoding, and caching |
| **Database Cache Reader** | |
| `R/read_external_data.R` | Reads NCBI, UniProt, and PubMed data from database cache |

---

## Chapter 3: Application Entry Point: app.R

The `app.R` file serves as the main entry point and orchestrates the application startup:

```r
# Load required packages
library(shiny)
library(bslib)       # Bootstrap 5 theming with dark mode
library(dplyr)
library(purrr)
library(stringr)
library(DT)
library(tools)
library(data.table)
library(sysfonts)
library(showtext)
library(fastmap)     # O(1) lookup data structures
library(memoise)     # Function memoisation
library(cachem)      # Caching backend for memoise
library(digest)      # Hash computation for cache keys
library(parallel)    # Multi-threaded QS loading
library(qs)          # Fast serialization (3-5x faster than RDS)
library(jsonlite)    # JSON handling
library(shinyWidgets)  # Enhanced Shiny inputs
library(leaflet)      # Interactive maps with marker clustering
library(tidygeocoder) # Geocoding via OpenStreetMap Nominatim
library(htmltools)    # HTML escaping for popup content

# rsconnect dependency detection
# These explicit library() calls are wrapped in if (FALSE) {} to ensure
# rsconnect detects all dependencies during deployment without actually
# loading them twice.
if (FALSE) {
  library(shiny)
  library(bslib)
  # ... (all packages listed for rsconnect detection)
}

# Load Roboto font from local file (faster than font_add_google)
font_add("Roboto", "www/fonts/Roboto-Regular.ttf")
showtext_auto()

# Source module files in dependency order
source("R/constants.R")
source("R/utils.R")
source("R/filter_utils.R")
source("R/data_prep.R")
source("R/tooltips.R")
source("R/mod_checkbox_filter.R")
source("R/server_table1.R")
source("R/server_table2.R")
source("R/fetch_trial_locations.R")
source("R/server_map.R")
source("R/ui.R")
source("R/server.R")

# Load data at startup (pre-computed for performance)
app_data <- load_and_prepare_data()
table1_display <- prepare_table1_display(...)

# Preload Table 2 if configured (eliminates tab-switch delay)
if (PRELOAD_TABLE2) {
  table2_data <- load_table2_data()
  table2_display <- prepare_table2_display(...)

  # Store preloaded data for server
  preloaded_table2 <- list(
    table2 = table2_data$table2,
    table2_display = table2_display,
    ct_info = table2_data$ct_info,
    registry_matches = table2_data$registry_matches,
    registry_rows = table2_data$registry_rows,
    sample_sizes = table2_data$sample_sizes,
    sample_sizes_hash = table2_data$sample_sizes_hash
  )
} else {
  preloaded_table2 <- NULL
}

# Compute dashboard statistics for About page
n_genes <- length(unique(app_data$table1$Gene))
n_publications <- length(unique(unlist(app_data$table1$References)))

# Build and run with statistics passed to UI
ui <- build_ui(n_genes, n_drugs, n_trials, n_publications)
server <- build_server(app_data, table1_display, preloaded_table2)
shinyApp(ui = ui, server = server)
```

### Startup Sequence

1. **Package Loading**: Essential packages are loaded individually rather than the full tidyverse for faster startup. Key packages include `bslib` for Bootstrap 5 theming, `qs` for fast serialization, `fastmap` for O(1) lookups, `memoise`/`cachem` for caching, and `digest` for hash computation.
2. **Font Setup**: Roboto font is loaded from local files using `showtext`, which is faster than using `font_add_google()`
3. **Sass Cache Setup**: bslib Sass compilation is cached to disk for faster subsequent startups
4. **CSS/JS Minification**: Static assets are minified at startup for production performance (CSS and JavaScript)
5. **Module Sourcing**: R files are sourced in dependency order—`constants.R` first, then utils, filter_utils, data prep, tooltips, checkbox filter modules, server modules (`server_table1.R`, `server_table2.R`), UI, and finally the main server orchestrator
6. **Data Preparation**: `load_and_prepare_data()` loads and preprocesses Table 1 data using QS format with multi-threaded reading
7. **Tooltip Pre-computation**: `prepare_table1_display()` generates HTML tooltips for all cells
8. **Table 2 Preloading**: When `PRELOAD_TABLE2 = TRUE` (default), Table 2 data is loaded at startup to eliminate tab-switch delay
9. **Statistics Computation**: Dashboard statistics (gene count, drug count, trial count, publication count) are computed for the About page
10. **App Construction**: UI and server are built with the pre-loaded data and statistics

### Sass Cache Configuration

Pre-caching bslib Sass compilation speeds up subsequent app restarts by reusing compiled CSS:

```r
# Pre-cache bslib Sass compilation for faster startup
bslib_cache_dir <- file.path(getwd(), ".bslib-cache")
if (!dir.exists(bslib_cache_dir)) {
  dir.create(bslib_cache_dir, recursive = TRUE)
}
options(
  sass.cache = cachem::cache_disk(
    dir = bslib_cache_dir,
    max_size = 50 * 1024^2,  # 50 MB
    max_age = 60 * 60 * 24 * 30  # 30-day cache
  )
)
```

### CSS and JavaScript Minification

The application minifies CSS and JavaScript at startup for production performance:

```r
minify_css <- function(input_path, output_path) {
  css <- readLines(input_path, warn = FALSE) |> paste(collapse = "\n")
  original_size <- nchar(css)

  # Remove comments
  css <- gsub("/\\*[\\s\\S]*?\\*/", "", css, perl = TRUE)
  # Remove newlines and collapse whitespace
  css <- gsub("\\s+", " ", css, perl = TRUE)
  # Remove spaces around special characters
  css <- gsub("\\s*([{};:,>~+])\\s*", "\\1", css, perl = TRUE)
  # Remove trailing semicolons before closing braces
  css <- gsub(";}", "}", css, fixed = TRUE)
  css <- trimws(css)

  writeLines(css, output_path)
  list(original = original_size, minified = nchar(css))
}

css_result <- minify_css("www/custom.css", "www/custom.min.css")
```

The minification achieves approximately 50-60% size reduction, with `custom.css` (2,484 lines) compressed to a single line in `custom.min.css`. Similarly, `custom.js` and `python_plot.js` are minified to their `.min.js` counterparts.

---

## Chapter 4: Data Layer

### Data Files

The application uses pre-processed data stored in QS format (3-5x faster than RDS) with automatic fallback to RDS for backward compatibility:

| File | Contents |
|------|----------|
| `table1_clean.qs` | Pre-cleaned gene data (Table 1) |
| `table2_clean.qs` | Pre-cleaned clinical trial data (Table 2) |
| `gene_info_results_df.qs` | NCBI gene information (IDs, proteins, aliases) for Table 1 |
| `gene_info_table2.qs` | NCBI gene information for Table 2 genetic targets |
| `prot_info_clean.qs` | UniProt protein information |
| `refs.qs` | Publication references for tooltips |
| `gwas_trait_names.qs` | GWAS trait abbreviation to full name mapping |
| `omim_info.csv` | OMIM phenotype information |

*Data files stored in data/qs/*

### QS Fast Serialization

The application uses the `qs` package as the primary data format for 3-5x faster loading with multi-threaded reading. The function `safe_read_data()` handles format detection with automatic RDS fallback:

```r
safe_read_data <- function(file_path) {
  # If path is .qs, try it directly
  if (grepl("\\.qs$", file_path, ignore.case = TRUE)) {
    if (file.exists(file_path)) {
      return(tryCatch(
        qs::qread(file_path, nthreads = parallel::detectCores()),
        error = function(e) {
          stop(sprintf("Failed to load qs file '%s': %s",
               file_path, e$message), call. = FALSE)
        }
      ))
    }
    # Fall back to .rds if .qs doesn't exist
    rds_path <- sub("\\.qs$", ".rds", file_path, ignore.case = TRUE)
    if (file.exists(rds_path)) {
      message(sprintf("QS file not found, falling back to RDS: %s", rds_path))
      return(readRDS(rds_path))
    }
  }
  stop(sprintf("File not found: %s", file_path), call. = FALSE)
}

# Alias for backward compatibility
safe_read_rds <- safe_read_data
```

To convert existing RDS files to QS format for faster loading:

```r
source("R/data_prep.R")
convert_rds_to_qs()  # Converts all DATA_PATHS to .qs format
```

The `qs` package provides significant performance improvements through multi-threaded serialization and optimized compression algorithms.

### data_prep.R Functions

#### load_and_prepare_data()

This is the primary data loading function called at application startup. It returns a named list containing all data needed for Table 1:

```r
list(
  table1 = table1,                    # Main gene data (data.table)
  gene_info_results_df = ...,         # NCBI gene info
  prot_info_clean = ...,              # UniProt protein info
  omics_df = ...,                     # Omics types with full names
  unique_gwas_traits = ...,           # Character vector of traits
  gwas_traits_df = ...,               # Data frame of traits
  gwas_trait_mapping = ...,           # Abbrev -> full name mapping
  gwas_trait_rows = ...,              # fastmap: row indices per trait
  omics_type_rows = ...,              # fastmap: row indices per omics
  omim_info = ...,                    # OMIM phenotype data
  refs = ...                          # Publication references
)
```

Key operations performed:

- Fixes empty "Mendelian Randomization" values to "No"
- Applies title case formatting to pathway and protein names
- Fixes "Wnt" to "WNT" and "proteomics" to "Proteomics"
- Pre-computes row indices for GWAS and omics filtering using `fastmap::fastmap()` for O(1) lookups
- Includes "(none found)" row indices for filtering genes without GWAS/omics evidence
- Converts to `data.table` for fast filtering operations

#### load_table2_data()

Loads clinical trial data. When `PRELOAD_TABLE2 = TRUE`, returns a reference to data preloaded at application startup; otherwise loads on demand when the user navigates to the Clinical Trials tab:

```r
list(
  table2 = table2,                    # Clinical trial data (data.table)
  ct_info = ct_info,                  # Trial name and primary outcome
  registry_matches = ...,             # Matched registry patterns
  registry_rows = ...,                # fastmap: row indices per registry
  gene_info_table2 = ...,             # NCBI gene info for Table 2
  gene_symbols_table2 = ...,          # Gene symbols vector
  sample_sizes = ...,                 # Pre-computed sample sizes
  sample_sizes_hash = ...             # Pre-computed digest hash for caching
)
```

Key operations performed:

- Pre-computes `sample_size_numeric` column for slider filtering
- Creates `registry_rows` fastmap for O(1) registry type lookups
- Sets data.table indices on frequently filtered columns
- Pre-computes digest hash of sample sizes for histogram caching

### Preloading Strategy

Table 2 (Clinical Trials) data can be preloaded at application startup to eliminate loading delays when users switch tabs.

#### Configuration

The preloading behavior is controlled by `PRELOAD_TABLE2` in `constants.R`:

```r
# Enable preloading (recommended for production)
PRELOAD_TABLE2 <- TRUE
```

#### Memory Optimization

When preloading is enabled, the application uses direct references to the preloaded data instead of creating per-session copies:

- **Direct Reference Pattern**: `load_table2()` returns a reference to the preloaded data rather than copying it
- **Memory Savings**: Saves 1-3MB per user session in multi-user deployments
- **Session Cleanup**: Uses `session$onSessionEnded()` to clean up session-specific reactive values

#### Trade-offs

- **Startup Time**: Preloading increases initial application startup time
- **Tab Switching**: Eliminates loading delays when switching to the Clinical Trials tab
- **Memory Footprint**: Shared data reference reduces per-session memory usage

---

## Chapter 5: Application Constants (constants.R)

The `constants.R` file centralizes all application-wide constants to avoid magic numbers and improve maintainability:

```r
# Data column indices
TABLE2_TRIAL_NAME_COL <- 5L
TABLE2_PRIMARY_OUTCOME_COL <- 12L

# Sample size filter bounds
SAMPLE_SIZE_MIN <- 15L
SAMPLE_SIZE_MAX <- 3156L

# Histogram configuration
HISTOGRAM_BREAKS <- 15L
HISTOGRAM_XLIM_MAX <- 3500L
HISTOGRAM_TICK_INTERVAL <- 500L

# Debounce delays (milliseconds)
SLIDER_DEBOUNCE_MS <- 500L
CHECKBOX_DEBOUNCE_MS <- 150L
DATATABLE_SEARCH_DELAY <- 500L

# DataTable configuration
DATATABLE_PAGE_LENGTH <- 10L
DATATABLE_SERVER_SIDE <- FALSE  # Client-side for small datasets

# Preload configuration
PRELOAD_TABLE2 <- TRUE  # Preload Table 2 at startup

# Cache configuration
MEMO_CACHE_SIZE <- 50 * 1024^2  # 50MB

# Data file paths (QS format, falls back to RDS)
DATA_PATHS <- list(
  table1_clean = "data/qs/table1_clean.qs",
  table2_clean = "data/qs/table2_clean.qs",
  gene_info = "data/qs/gene_info_results_df.qs",
  prot_info = "data/qs/prot_info_clean.qs",
  refs = "data/qs/refs.qs",
  omim_info = "data/csv/omim_info.csv"
)

# Clinical trial registry patterns and URLs
REGISTRY_PATTERNS <- c("NCT", "ISRCTN", "ACTRN", "ChiCTR")
REGISTRY_URLS <- list(
  NCT = "https://clinicaltrials.gov/study/",
  ISRCTN = "https://www.isrctn.com/",
  ...
)

# External URLs
NCBI_GENE_BASE_URL <- "https://www.ncbi.nlm.nih.gov/gene/"
PUBMED_BASE_URL <- "https://pubmed.ncbi.nlm.nih.gov/"

# Display placeholders
PLACEHOLDER_NONE_FOUND <- "(none found)"
PLACEHOLDER_UNKNOWN <- "(unknown)"
PLACEHOLDER_REFERENCE_NEEDED <- "(reference needed)"
COMPLETION_UNPUBLISHED <- "Completed (unpublished)"

# Clinical Trials Map Configuration
MAP_CACHE_PATH <- "data/qs/geocoded_trials.qs"  # Geocoded trials cache file
MAP_CT_API_BASE_URL <- "https://clinicaltrials.gov/api/v2/studies/"  # API v2 base URL
MAP_API_DELAY_MS <- 100L  # API request delay (ms) to avoid rate limiting
MAP_DEFAULT_LAT <- 30     # Default map center latitude
MAP_DEFAULT_LNG <- 0      # Default map center longitude
MAP_DEFAULT_ZOOM <- 2     # Default map zoom level
MAP_HEIGHT_PX <- 700L     # Map container height in pixels
```

---

## Chapter 6: Tooltip System

### Overview

The application uses [Tippy.js](https://atomiks.github.io/tippyjs/) for interactive tooltips. The `tooltips.R` module generates HTML markup with `data-tippy-content` attributes that are initialized by JavaScript.

### Memoisation

Tooltip lookups are cached using `memoise` with an in-memory cache backend:

```r
# 50MB in-memory cache for tooltip lookups
memo_cache <- cachem::cache_mem(max_size = 50 * 1024^2)

# Memoised version of reference tooltip lookup
get_ref_tooltip_info_memo <- memoise::memoise(
  get_ref_tooltip_info,
  cache = memo_cache
)
```

### Key Functions in tooltips.R

#### get_cell_type_tooltip()

Maps brain cell type abbreviations to full names:

```r
cell_type_map <- list(
  "EC" = "Endothelial Cells",
  "SMC" = "Smooth Muscle Cells",
  "VSMC" = "Vascular Smooth Muscle Cells",
  "AC" = "Astrocytes",
  "MG" = "Microglia",
  "OL" = "Oligodendrocytes",
  "PC" = "Pericytes",
  "FB" = "Fibroblasts"
)
```

#### prepare_table1_display()

The main function that transforms `table1` by adding Tippy.js tooltip HTML to all interactive columns:

- **Column 1 (Gene Symbol)**: Links to NCBI Gene with tooltip showing gene ID, protein, and aliases
- **Column 2 (Protein)**: Links to UniProt with tooltip showing accession number
- **Column 7 (OMIM)**: Links to OMIM with tooltip showing phenotype, inheritance, gene/locus
- **Column 8 (Brain Cell Types)**: Tooltips for cell type abbreviations
- **Column 10 (References)**: Links to PubMed with publication details
- **GWAS Trait**: Tooltips showing full trait names
- **Evidence From Other Omics Studies**: Tooltips for omics type abbreviations

---

## Chapter 7: Shiny Module: Checkbox Filters

### Module Pattern

The `mod_checkbox_filter.R` file implements reusable Shiny modules for checkbox filter groups. This follows the Shiny module pattern with UI and server functions sharing a namespace.

### checkbox_filter_ui()

Creates a checkbox group input wrapped in a filter box container:

```r
checkbox_filter_ui <- function(id, label, choices,
                               selected = NULL, has_show_all = FALSE) {
  ns <- shiny::NS(id)
  shiny::div(
    id = id,
    class = "filter-box",
    shiny::checkboxGroupInput(
      ns("filter"), label, choices = choices, selected = selected
    )
  )
}
```

### checkbox_filter_server()

Implements "Show All" toggle behavior:

- Selecting "Show All" deselects all other options
- Selecting any other option deselects "Show All"
- Uses `reactiveVal` to track previous selection state
- Uses `freezeReactiveValue()` to prevent cascading invalidation
- Returns a **debounced** reactive (150ms delay) to avoid rapid re-filtering

---

## Chapter 8: User Interface (ui.R)

### Structure

The UI is built by the `build_ui()` function which accepts statistics parameters for the About page:

```r
build_ui <- function(
  n_genes = 0L,    # Number of unique genes
  n_drugs = 0L,    # Number of unique drugs
  n_trials = 0L,   # Number of clinical trials
  n_pubs = 0L      # Number of publications
)
```

It returns a `fluidPage` containing:

1. **Head**: External CSS and JavaScript (minified custom styles, Tippy.js)
2. **Header**: ICM logo, copyright, and license information
3. **Title Section**: Main application title
4. **Content Wrapper**:
   - Sidebar filters (conditional on active tab)
   - Main content area with tabbed interface

### Tab Structure

| Tab | Content |
|-----|---------|
| About | Welcome message, dashboard statistics, citation info, contact details |
| Gene Table | Filterable DataTable of putative causal genes |
| Phenogram | Interactive chromosome visualization (iframe) |
| Clinical Trials Table | Filterable DataTable of clinical trials |
| Clinical Trials Visualization | Timeline visualization (iframe) |
| Trials Map | Interactive Leaflet map showing clinical trial research sites (NCT IDs only) |

### Gene Table Filters

- **Mendelian Randomization**: Yes/No binary filter
- **GWAS Traits**: Multi-select with "Show All" and "None Found" options. Includes: SVS, BG-PVS, WMH, HIP-PVS, PSMD, extreme-cSVD, lacunes, stroke, NODDI, FA, MD, lacunar stroke, small vessel stroke
- **Evidence From Other Omics Studies**: Multi-select with "Show All" and "None Found" options. Includes: EWAS, TWAS, PWAS, Proteomics, MENTR

### Clinical Trials Table Filters

- **Genetic Evidence**: Yes/No binary filter
- **Clinical Trial Registry**: NCT, ISRCTN, ACTRN, ChiCTR
- **Clinical Trial Phase**: I, II, III
- **SVD Population**: CAA, Cognitive Impairment, Stroke, SVD
- **Target Sample Size**: Slider with histogram visualization
- **Sponsor Type**: Academic, Industry

### bslib Bootstrap 5 Integration

The application uses `bslib` (Bootstrap 5) for modern theming with light and dark mode support. This replaced the previous `fluidPage`-based UI in December 2024.

#### Theme Definitions

Two themes are defined in `R/ui.R` (lines 14-47):

```r
light_theme <- bslib::bs_theme(
  version = 5,
  bg = "#ffffff",
  fg = "#1f2937",
  primary = "#2d287a",
  secondary = "#667eea",
  success = "#008000",
  danger = "#ff0000",
  base_font = "Roboto, sans-serif",
  heading_font = "Roboto, sans-serif",
  "navbar-bg" = "#f8f9fc",
  "navbar-light-color" = "#1f2937"
)
```

```r
dark_theme <- bslib::bs_theme(
  version = 5,
  bg = "#121212",
  fg = "#e0e0e0",
  primary = "#6366f1",
  secondary = "#818cf8",
  success = "#4ade80",
  danger = "#f87171",
  base_font = "Roboto, sans-serif",
  heading_font = "Roboto, sans-serif",
  "navbar-bg" = "#1a1a2e",
  "navbar-dark-color" = "#e0e0e0"
)
```

#### Dark Mode Toggle

The dark mode toggle is placed in the navbar using `bslib::input_dark_mode()`:

```r
bslib::nav_spacer(),
bslib::nav_item(
  bslib::input_dark_mode(id = "dark_mode", mode = "light")
),
```

Theme switching is handled reactively in the server (R/server.R lines 30-38):

```r
shiny::observeEvent(
  input$dark_mode,
  {
    session$setCurrentTheme(
      if (isTRUE(input$dark_mode)) dark_theme else light_theme
    )
  },
  ignoreNULL = FALSE
)
```

#### Page Structure with bslib

The UI is built using `bslib::page_navbar()` instead of the legacy `fluidPage`:

```r
build_ui <- function(n_genes, n_drugs, n_trials, n_pubs) {
  bslib::page_navbar(
    id = "tabs",
    title = shiny::tags$picture(
      shiny::tags$source(srcset = "images/icm_logo.webp", type = "image/webp"),
      shiny::tags$img(src = "images/icm_logo.png", alt = "ICM Logo", height = "40")
    ),
    window_title = "ICM Cerebral SVD Dashboard",
    theme = light_theme,
    fillable = FALSE,
    ...
  )
}
```

### Glassmorphism Design System

The dark mode implements a modern glassmorphism-inspired design in `www/custom.css` (2,484 lines). This provides a visually striking interface with translucent elements and blur effects.

#### CSS Architecture

The CSS uses the `[data-bs-theme="dark"]` attribute selector (automatically set by bslib) to apply dark mode styles:

```css
[data-bs-theme="dark"] body {
  background: linear-gradient(135deg, #0f0c29 0%, #302b63 50%, #24243e 100%);
  min-height: 100vh;
}

[data-bs-theme="dark"] .main-container {
  background: var(--svd-bg-card);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  border: 1px solid var(--svd-border);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
}
```

#### Component Theming

The CSS provides comprehensive styling for all UI components:

- **Cards**: Semi-transparent with blur and subtle borders
- **Value Boxes**: Gradient backgrounds with glow effects
- **DataTables**: Dark backgrounds with themed scrollbars
- **Tippy Tooltips**: Dark theme with enhanced shadows
- **Form Controls**: Dark inputs with focus glow effects
- **Sidebar**: Subtle glass effect with blurred backdrop

#### Graceful Fallback

For browsers that don't support `backdrop-filter`, a fallback provides solid backgrounds:

```css
@supports not (backdrop-filter: blur(10px)) {
  [data-bs-theme="dark"] .main-container {
    background: rgba(26, 26, 46, 0.95);
  }
}
```

---

## Chapter 9: Server Logic (Modular Architecture)

The server logic is split across four files for maintainability:

- `server.R` — Main orchestrator that composes the server modules
- `server_table1.R` — Table 1 (Gene Table) filtering and rendering
- `server_table2.R` — Table 2 (Clinical Trials) filtering, histogram, and rendering
- `server_map.R` — Clinical Trials Map with lazy loading and leafletProxy updates

### Main Server Orchestrator (server.R)

The `build_server()` function creates a server function that composes the table-specific modules:

```r
build_server <- function(app_data, table1_display, preloaded_table2 = NULL) {
  function(input, output, session) {
    # Extract data from app_data
    table1 <- app_data$table1
    gwas_trait_rows <- app_data$gwas_trait_rows
    omics_type_rows <- app_data$omics_type_rows

    # Theme switching (bslib dark mode)
    shiny::observeEvent(input$dark_mode, {...}, ignoreNULL = FALSE)

    # Python plot handler
    setup_python_plot_handler(input, session)

    # Table 2 data handling (preloaded or lazy loaded)
    # When preloaded_table2 is provided, use direct reference (no copying)
    if (!is.null(preloaded_table2)) {
      load_table2 <- shiny::reactive({ preloaded_table2 })
    } else {
      # Lazy loading: create reactiveVals to track loading state
      table2_reactive_vals <- create_table2_reactive_vals()
      load_table2 <- build_table2_loader(...)
      setup_table2_lazy_load_trigger(input, load_table2)
    }

    # Initialize all filter modules
    filters <- initialize_filter_modules()

    # Debounce slider input
    sample_size_filter_debounced <- shiny::debounce(
      shiny::reactive(input$sample_size_filter),
      SLIDER_DEBOUNCE_MS
    )

    # Table 1 server logic
    filtered_data <- build_table1_filtered_data(...)
    output$filter_message_table1 <- build_table1_filter_message(...)
    output$firstTable <- build_table1_datatable(filtered_data)

    # Table 2 server logic
    output$sample_size_histogram <- build_sample_size_histogram(...)
    filtered_data2 <- build_table2_filtered_data(...)
    output$filter_message_table2 <- build_table2_filter_message(...)
    output$secondTable <- build_table2_datatable(filtered_data2)

    # Clinical Trials Map server logic
    map_data_loader <- build_map_data_loader(load_table2)
    setup_map_lazy_load_trigger(input, map_data_loader)
    output$trials_map <- build_trials_map(map_data_loader)
    output$map_stats <- build_map_stats(map_data_loader)

    # Configure output suspension
    configure_output_options(output)
  }
}
```

### Filtering Logic

#### Table 1 Filtering

The `filtered_data` reactive applies filters sequentially:

```r
filtered_data <- shiny::reactive({
  filtered_table1 <- data.table::copy(table1)[, row_id := .I]

  # MR filter (skip if both selected)
  if (length(mr_filter()) == 1) {
    filtered_table1 <- filtered_table1[
      get("Mendelian Randomization") %in% mr_filter()
    ]
  }

  # GWAS trait filter using fastmap $mget for O(1) lookups
  if (!"all" %in% gwas_trait_filter()) {
    matching_rows <- unique(unlist(
      gwas_trait_rows$mget(gwas_trait_filter())
    ))
    filtered_table1 <- filtered_table1[row_id %in% matching_rows]
  }

  # Omics filter using fastmap $mget for O(1) lookups
  if (!"all" %in% omics_filter()) {
    matching_rows <- unique(unlist(
      omics_type_rows$mget(omics_filter())
    ))
    filtered_table1 <- filtered_table1[row_id %in% matching_rows]
  }

  # Return display version with row numbers
  result <- table1_display[filtered_table1$row_id, ]
  cbind(data.frame(`#` = seq_len(nrow(result))), result)
}) |> bindCache(mr_filter(), gwas_trait_filter(), omics_filter())
```

### Map Server Module (server_map.R)

The Clinical Trials Map tab uses lazy loading to defer data fetching until the user accesses the tab:

```r
# Map data loader with lazy loading and caching
map_data_loader <- build_map_data_loader(load_table2)

# Setup lazy load trigger when tab is accessed
setup_map_lazy_load_trigger(input, map_data_loader)

# Map rendering with markers
output$trials_map <- build_trials_map(map_data_loader)

# Statistics display
output$map_stats <- build_map_stats(map_data_loader)
```

**Note:** The actual implementation uses `build_trials_map()` which is a legacy wrapper that creates a complete map with markers. For optimized implementations, the split pattern with `build_trials_map_base()` and `build_map_marker_observer()` using `leafletProxy()` can be used for incremental marker updates.

Key functions in `server_map.R`:

| Function | Purpose |
|----------|---------|
| `build_map_data_loader()` | Creates reactive with `reactiveVal` caching for geocoded trial locations |
| `build_trials_map_base()` | Renders base Leaflet map with CartoDB.Positron tiles |
| `build_map_marker_observer()` | Uses `leafletProxy()` to add markers without re-rendering entire map |
| `build_map_stats()` | Displays count of mapped sites and unique trials |
| `setup_map_lazy_load_trigger()` | Rate-limited observer that triggers data loading on tab access |

### Output Options Configuration

The `configure_output_options()` function sets `suspendWhenHidden = TRUE` for all outputs to improve performance by suspending outputs that are not visible:

```r
configure_output_options <- function(output) {
  shiny::outputOptions(output, "firstTable", suspendWhenHidden = TRUE)
  shiny::outputOptions(output, "filter_message_table1", suspendWhenHidden = TRUE)
  shiny::outputOptions(output, "sample_size_histogram", suspendWhenHidden = TRUE)
  shiny::outputOptions(output, "filter_message_table2", suspendWhenHidden = TRUE)
  shiny::outputOptions(output, "secondTable", suspendWhenHidden = TRUE)
  shiny::outputOptions(output, "trials_map", suspendWhenHidden = TRUE)
  shiny::outputOptions(output, "map_stats", suspendWhenHidden = TRUE)
}
```

---

## Chapter 10: Performance Optimizations

### Pre-computation

- **Row Indices**: GWAS trait, omics type, and registry type row indices are pre-computed at startup using `fastmap` for O(1) filtering
- **Tooltip HTML**: All tooltip markup is generated once at startup, not on each render
- **Sample Sizes**: Numeric sample sizes and their digest hash are pre-computed for histogram caching
- **Drug Group Indices**: Pre-computed in R for Table 2 row merging, avoiding JavaScript recalculation
- **Dashboard Statistics**: Gene, drug, trial, and publication counts computed once at startup
- **Constants**: All configuration values centralized in `constants.R` to avoid repeated lookups
- **Vectorized Index Building**: GWAS trait and omics type row indices are built using `data.table::rbindlist()` combined with `split()` instead of row-by-row `vapply` loops, providing significant startup performance improvement

### Caching

- **bindCache()**: Filtered data reactives are cached based on filter inputs
- **Preloaded Data**: Table 2 data is preloaded at startup when `PRELOAD_TABLE2 = TRUE`, using direct references instead of per-session copies. This saves 1-3MB of memory per user session in multi-user deployments
- **renderCachedPlot**: Sample size histogram uses `renderCachedPlot()` with `sizeGrowthRatio()` for size-responsive caching, avoiding recomputation when plot dimensions change
- **Memoisation**: Reference tooltip lookups are memoised using `memoise` with an in-memory cache (`cachem`)
- **Histogram Caching**: Uses pre-computed digest hash to avoid recomputing on each render

### Debouncing

- **Filter Modules**: All checkbox filters are debounced (`CHECKBOX_DEBOUNCE_MS` = 150ms) to avoid rapid re-filtering
- **Sample Size Slider**: Debounced (`SLIDER_DEBOUNCE_MS` = 500ms) to avoid filtering on every slider movement
- **DataTable Search**: Search delay (`DATATABLE_SEARCH_DELAY` = 500ms) to prevent excessive queries

### Map-Specific Optimizations

- **Lazy Loading**: Map data is only fetched when the Clinical Trials Map tab is first accessed, not at application startup
- **leafletProxy**: Markers are added/updated using `leafletProxy()` instead of re-rendering the entire map, reducing browser repaints
- **Geocoded Data Cache**: Geocoded coordinates are cached to `data/qs/geocoded_trials.qs` with SHA256 integrity verification to avoid redundant geocoding
- **Cache Invalidation**: Cache includes a hash of NCT IDs; when trials change, the cache is automatically invalidated
- **Parallel API Requests**: Uses `future.apply::future_lapply()` with configurable workers for concurrent API requests
- **Rate Limiting**: 100ms delay between API request batches and 10-second minimum interval between tab-switch triggers
- **Exponential Backoff**: Failed API requests retry with exponential backoff (1s, 2s, 4s) up to 3 attempts
- **Marker Clustering**: Uses Leaflet.markercluster to efficiently render many markers without overwhelming the browser
- **Coordinate Jittering**: Duplicate coordinates are jittered in a circular pattern to prevent marker stacking

### Browser Performance Optimization

The application includes WebKit-specific optimizations to prevent horizontal scrolling stuttering in DataTables.

#### RAF-Based Scroll Synchronization

- **Request Animation Frame**: Scroll synchronization between the top scrollbar and table body uses `requestAnimationFrame` instead of direct event handlers to align with browser paint cycles
- **Source Tracking**: The sync function tracks which element initiated the scroll to prevent circular update loops
- **Cooldown Period**: A 16ms cooldown (approximately one frame at 60fps) prevents cascade syncs and layout thrashing

#### CSS Performance Containment

- **Layout Containment**: Uses `contain: layout paint` on scroll containers to isolate layout calculations
- **Scroll Hints**: `will-change: scroll-position` and `-webkit-overflow-scrolling: touch` provide browser optimization hints
- **During-Scroll Styling**: Hover effects and transitions are disabled during active scrolling to reduce paint operations

---

## Chapter 11: Testing Infrastructure

The application includes a comprehensive test suite located in `tests/test_all.R`.

### Test Coverage

The test suite validates:

- All constants are defined with correct types and reasonable values
- Data loading functions and QS/RDS format handling
- GWAS trait extraction and omics type mapping
- Filter module behavior and debouncing
- Tooltip HTML generation and cell type mapping
- Reference formatting and external data fetching

### Running Tests

```r
# Run all tests
source("tests/test_all.R")

# Run tests with the testthat framework
testthat::test_file("tests/test_all.R")

# Run tests with coverage (requires covr package)
covr::file_coverage("tests/test_all.R")
```

---

## Chapter 12: Adding New Features

### Adding a New Filter

1. Add UI element in `ui.R` using `checkbox_filter_ui()`
2. Initialize server module in `server.R` within `initialize_filter_modules()`
3. Add filtering logic to the appropriate module (`server_table1.R` or `server_table2.R`)
4. Update `bindCache()` to include new filter
5. Add to filter message display in the corresponding server module
6. Add any new constants to `constants.R`
7. Add unit tests in `tests/test_all.R`

### Adding a New Column with Tooltips

1. Add data to the appropriate `load_*_data()` function in `data_prep.R`
2. Create tooltip generation function in `tooltips.R`
3. Call tooltip function in `prepare_table*_display()`
4. Update DataTable column definitions if needed
5. Add unit tests in `tests/test_all.R`

### Adding a New Tab

1. Add `bslib::nav_panel()` in `ui.R` within the `page_navbar()`
2. Add conditional sidebar if filters are needed
3. Create a new server module file (e.g., `server_newtab.R`) following the pattern of existing modules
4. Source the new module in `app.R`
5. Integrate the module in `server.R`
6. Consider lazy loading for heavy data (see `server_map.R` for a lazy-loading example)
7. Add any new constants to `constants.R`

**Example: Clinical Trials Map Implementation**

The Clinical Trials Map tab demonstrates best practices for lazy-loaded tabs:

- **UI**: Added `bslib::nav_panel()` with `leaflet::leafletOutput()` in `ui.R`
- **Server Module**: Created `server_map.R` with `build_map_data_loader()` using `reactiveVal` caching
- **Lazy Loading**: `setup_map_lazy_load_trigger()` observes tab changes and triggers data loading only when needed
- **Performance**: Uses `leafletProxy()` to update markers without full map re-renders
- **External Data**: Created `fetch_trial_locations.R` for API integration with caching

---

## Chapter 13: Dependencies

### R Packages

| Package | Purpose |
|---------|---------|
| shiny | Web application framework |
| bslib | Bootstrap 5 theming and dark mode |
| dplyr | Data manipulation |
| purrr | Functional programming utilities |
| stringr | String manipulation |
| DT | Interactive DataTables |
| data.table | Fast data operations |
| tools | Title case formatting |
| sysfonts | Font loading |
| showtext | Custom font rendering |
| fastmap | O(1) lookup data structures |
| memoise | Function memoisation |
| cachem | Caching backend for memoise and Sass |
| digest | Hash computation for cache keys |
| testthat | Unit testing framework (development) |
| qs | Fast serialization (3-5x faster than RDS) |
| promises | Async programming for reactive operations |
| future | Parallel processing backend |
| leaflet | Interactive maps with marker clustering |
| tidygeocoder | Geocoding via OpenStreetMap Nominatim |
| httr2 | HTTP requests with timeout and retry logic |
| htmltools | HTML escaping for popup content |
| future.apply | Parallel API requests with `future_lapply()` |

---

## Chapter 14: Troubleshooting

### Common Issues

**Tooltips not appearing**
: Check that `initializeTippy()` is called in `drawCallback` and that Tippy.js is loaded

**Slow filtering**
: Ensure `bindCache()` includes all filter dependencies

**Missing data**
: Verify QS files exist in `data/qs/` (or RDS files in `data/rds/` as fallback)

**Font issues**
: Check that Roboto font file exists in `www/fonts/`

**Table 2 slow to load**
: Ensure `PRELOAD_TABLE2 = TRUE` in `constants.R` for startup preloading

**WebKit horizontal scroll stutter**
: Ensure `custom.js` RAF-based scroll synchronization is active and CSS containment rules are applied

**High memory in multi-user deployment**
: Verify `PRELOAD_TABLE2 = TRUE` is set to use direct references instead of per-session copies

**Map not loading / showing "Loading trial location data..."**
: Check that ClinicalTrials.gov API is accessible; verify network connectivity; check console for HTTP errors; the cache may need to be regenerated if `data/qs/geocoded_trials.qs` is corrupted

**Map cache integrity check failed**
: Delete `data/qs/geocoded_trials.qs` and `data/qs/geocoded_trials.qs.sha256` to force a fresh fetch and geocode cycle

**Geocoding returning many NA coordinates**
: OpenStreetMap Nominatim has rate limits; ensure `MAP_API_DELAY_MS` is set appropriately (default 100ms); some facility locations may not be geocodable

**Map markers stacked on top of each other**
: The jittering function should spread markers at identical coordinates in a circular pattern; if still stacked, increase the jitter radius in `jitter_duplicate_coordinates()`

### Debug Tips

- Use `message()` calls to trace data loading and filtering
- Check browser console for JavaScript errors
- Use RStudio's Shiny debugging tools
- Inspect reactive graph with `reactlog` package
- Run the test suite: `source("tests/test_all.R")`
- Check constants are correctly loaded: verify values from `constants.R`

---

## Chapter 15: Clinical Trials Map Module

### Map Overview

The Clinical Trials Map is an interactive visualization showing the geographic distribution of clinical trial research sites. It displays only trials registered on ClinicalTrials.gov (NCT IDs) and provides:

- Interactive Leaflet map with marker clustering
- Rich HTML popups with trial metadata and status badges
- Color-coded status indicators (recruiting, active, completed, terminated)
- Links to ClinicalTrials.gov trial pages

### Data Flow

```
Table 2 (Clinical Trials)
         ↓
extract_nct_ids() → NCT IDs only
         ↓
fetch_all_trial_locations() → ClinicalTrials.gov API v2
         ↓
geocode_locations() → tidygeocoder + OpenStreetMap Nominatim
         ↓
prepare_map_data() → Join with Table 2 metadata, generate popups
         ↓
build_map_marker_observer() → Render markers via leafletProxy
```

### API Integration

The `fetch_trial_locations.R` module queries the ClinicalTrials.gov API v2 to retrieve:

- Facility name, city, state, country
- Trial title (briefTitle)
- Recruitment status (overallStatus)

**API Request Configuration:**

```r
# HTTP configuration
http_timeout_seconds <- 30L      # Request timeout
max_retry_attempts <- 3L         # Retry count on failure
retry_base_delay_seconds <- 1L   # Base delay for exponential backoff

# Rate limiting
MAP_API_DELAY_MS <- 100L         # Delay between batch requests
```

**Exponential Backoff:**

Failed requests retry with delays of 1s, 2s, 4s (exponential backoff). Rate limiting (HTTP 429) responses respect the `Retry-After` header.

**Parallel Processing:**

```r
fetch_all_trial_locations <- function(nct_ids, n_workers = 4L, chunk_delay_ms = MAP_API_DELAY_MS)
```

Uses `future.apply::future_lapply()` with configurable parallel workers. Processes NCT IDs in chunks with delays between batches.

### Geocoding

The `geocode_locations()` function converts city/country strings to latitude/longitude coordinates using tidygeocoder with the OpenStreetMap Nominatim service:

```r
geocoded <- tidygeocoder::geocode(
  geocode_df,
  address = location_string,
  method = "osm",
  quiet = TRUE
)
```

- Deduplicates location strings before geocoding to avoid redundant API calls
- Logs geocoding success rate for debugging
- Gracefully handles missing or ungeocidable locations

### Caching Strategy

**Cache Structure:**

```r
cache_data <- list(
  locations = geocoded_locations_df,
  hash = digest::digest(sort(nct_ids)),  # For invalidation
  timestamp = Sys.time()
)
```

**Integrity Verification:**

The cache uses SHA256 hash verification to prevent deserialization attacks:

```r
# Save with integrity hash
save_cache_with_integrity <- function(data, cache_path) {
  qs::qsave(data, cache_path)
  hash <- digest::digest(file = cache_path, algo = "sha256")
  writeLines(hash, paste0(cache_path, ".sha256"))
}

# Load with verification
load_cache_with_integrity <- function(cache_path) {
  # Verify hash BEFORE deserialization
  file_hash <- digest::digest(file = cache_path, algo = "sha256")
  if (!identical(file_hash, stored_hash)) {
    warning("Cache integrity check failed")
    return(NULL)
  }
  qs::qread(cache_path)
}
```

**Cache Invalidation:**

The cache is automatically invalidated when the set of NCT IDs changes (hash mismatch). To force a refresh, set `force_refresh = TRUE` in `load_or_fetch_geocoded_trials()`.

### Map Server Functions

**server_map.R** contains the following key functions:

| Function | Description |
|----------|-------------|
| `build_map_data_loader()` | Creates a lazy-loading reactive with `reactiveVal` caching |
| `build_trials_map_base()` | Renders the initial Leaflet map with CartoDB.Positron tiles |
| `build_trials_map()` | Legacy wrapper that creates a complete map with markers |
| `build_map_marker_observer()` | Uses `leafletProxy()` to efficiently update markers |
| `build_map_stats()` | Renders statistics showing site count and trial count |
| `setup_map_lazy_load_trigger()` | Rate-limited observer for tab-switch data loading |

**Marker Cluster Configuration:**

```r
map_cluster_options <- leaflet::markerClusterOptions(
  showCoverageOnHover = TRUE,
  zoomToBoundsOnClick = TRUE,
  spiderfyOnMaxZoom = TRUE,
  removeOutsideVisibleBounds = TRUE,
  maxClusterRadius = 50
)
```

### Popup Content Generation

The `build_popup_content_vectorized()` function generates HTML popups using vectorized operations for performance:

**Popup Structure:**

```html
<div class='map-popup'>
  <div class='popup-title'>Trial Title</div>
  <div class='popup-status popup-status-recruiting'>Recruiting</div>
  <div class='popup-divider'></div>
  <div class='popup-trial-info'>
    <div class='popup-info-row'><span class='popup-label'>Drug:</span> Drug Name</div>
    <div class='popup-info-row'>Phase: III • Sponsor: Academic</div>
    <div class='popup-info-row'><span class='popup-label'>Sample Size:</span> 500</div>
  </div>
  <div class='popup-divider'></div>
  <div class='popup-facility'>📍 Facility Name</div>
  <div class='popup-location'>City, State, Country</div>
  <div class='popup-divider'></div>
  <a href='https://clinicaltrials.gov/study/NCT...' class='popup-link'>
    View on ClinicalTrials.gov →
  </a>
</div>
```

**Status Badge Colors:**

| Status | CSS Class | Color |
|--------|-----------|-------|
| Recruiting, Enrolling by Invitation | `popup-status-recruiting` | Green |
| Active Not Recruiting, Not Yet Recruiting | `popup-status-active` | Blue |
| Completed | `popup-status-completed` | Gray |
| Terminated, Withdrawn, Suspended | `popup-status-terminated` | Red |
| Unknown | `popup-status-unknown` | Gray |

All text content is HTML-escaped using `htmltools::htmlEscape()` to prevent XSS vulnerabilities in popup content.
