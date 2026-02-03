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
|-- app.R                                      329
|-- python_plot.py                           1,506
|-- R/
|   |-- tooltips.R                             733
|   |-- data_prep.R                            642
|   |-- ui.R                                   627
|   |-- server_table2.R                        618
|   |-- utils.R                                398
|   |-- filter_utils.R                         304
|   |-- server_table1.R                        288
|   |-- phenogram.R                            265
|   |-- fetch_pubmed_data.R                    257
|   |-- server.R                               262
|   |-- clean_table1.R                         239
|   |-- fetch_uniprot_data.R                   232
|   |-- fetch_ncbi_gene_data.R                 178
|   |-- mod_checkbox_filter.R                  167
|   |-- constants.R                            150
|   |-- fetch_omim_data.R                       95
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
|   +-- trigger_update.R         # Regenerate QS from database
|-- tests/
|   +-- test_all.R               # Unit tests (20KB)
|-- www/
|   |-- custom.css                           2,045
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
                                 Total:    11,268
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
| `R/phenogram.R` | Generates phenogram visualization data and SVG output |
| `R/fetch_uniprot_data.R` | Fetches protein information from UniProt API |
| `R/fetch_pubmed_data.R` | Fetches publication references from PubMed API |
| `R/fetch_ncbi_gene_data.R` | Fetches gene information from NCBI Gene API |
| `R/fetch_omim_data.R` | Fetches phenotype data from OMIM API |
| `R/clean_table1.R` | Cleans and preprocesses raw Table 1 (gene) data |
| `R/clean_table2.R` | Cleans and preprocesses raw Table 2 (clinical trial) data |

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
source("R/ui.R")
source("R/server.R")

# Load data at startup (pre-computed for performance)
app_data <- load_and_prepare_data()
table1_display <- prepare_table1_display(...)

# Preload Table 2 if configured (eliminates tab-switch delay)
if (PRELOAD_TABLE2) {
  table2_data <- load_table2_data()
}

# Compute dashboard statistics for About page
n_genes <- length(unique(app_data$table1$Gene))
n_publications <- length(unique(unlist(app_data$table1$References)))
table2_stats <- qs::qread("data/qs/table2_clean.qs", nthreads = detectCores())
n_trials <- length(unique(table2_stats$`Registry ID`))
n_drugs <- length(unique(table2_stats$Drug))
rm(table2_stats)  # Free memory immediately

# Build and run with statistics passed to UI
ui <- build_ui(n_genes, n_drugs, n_trials, n_publications)
server <- build_server(app_data, table1_display, table2_data)
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

The minification achieves approximately 50-60% size reduction, with `custom.css` (2,045 lines) compressed to a single line in `custom.min.css`. Similarly, `custom.js` and `python_plot.js` are minified to their `.min.js` counterparts.

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

Theme switching is handled reactively in the server (R/server.R lines 28-32):

```r
shiny::observe({
  session$setCurrentTheme(
    if (isTRUE(input$dark_mode)) dark_theme else light_theme
  )
})
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

The dark mode implements a modern glassmorphism-inspired design in `www/custom.css` (2,045 lines). This provides a visually striking interface with translucent elements and blur effects.

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

The server logic is split across three files for maintainability:

- `server.R` — Main orchestrator that composes the server modules
- `server_table1.R` — Table 1 (Gene Table) filtering and rendering
- `server_table2.R` — Table 2 (Clinical Trials) filtering, histogram, and rendering

### Main Server Orchestrator (server.R)

The `build_server()` function creates a server function that composes the table-specific modules:

```r
build_server <- function(app_data, table1_display) {
  function(input, output, session) {
    # Extract data from app_data
    table1 <- app_data$table1
    gwas_trait_rows <- app_data$gwas_trait_rows
    omics_type_rows <- app_data$omics_type_rows

    # Python plot handler
    setup_python_plot_handler(input, session)

    # Table 2 preloading setup (uses direct reference when PRELOAD_TABLE2 = TRUE)
    table2_data <- load_table2()  # Returns reference to preloaded data
    table2_reactive_vals <- create_table2_reactive_vals(table2_data)

    # Session cleanup
    session$onSessionEnded(function() {
      rm(table2_reactive_vals)
    })

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

1. Add `tabPanel` in `ui.R` within the `tabsetPanel`
2. Add conditional sidebar if filters are needed
3. Create a new server module file (e.g., `server_newtab.R`) following the pattern of existing modules
4. Source the new module in `app.R`
5. Integrate the module in `server.R`
6. Consider lazy loading for heavy data
7. Add any new constants to `constants.R`

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

### Debug Tips

- Use `message()` calls to trace data loading and filtering
- Check browser console for JavaScript errors
- Use RStudio's Shiny debugging tools
- Inspect reactive graph with `reactlog` package
- Run the test suite: `source("tests/test_all.R")`
- Check constants are correctly loaded: verify values from `constants.R`
