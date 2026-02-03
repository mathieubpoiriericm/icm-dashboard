# maRco Package Developer Documentation

**Version 0.11.0** | Developer Guide for the maRco R Package

This documentation covers the architecture, conventions, and APIs of the maRco package, a specialized R package providing helper functions for the Small Vessel Disease (SVD) Dashboard application.

---

## Table of Contents

- [Introduction](#introduction)
  - [Overview](#overview)
  - [Package Philosophy](#package-philosophy)
  - [Getting Started](#getting-started)
- [Package Architecture](#package-architecture)
  - [Directory Structure](#directory-structure)
  - [Module Overview](#module-overview)
  - [Data Flow Architecture](#data-flow-architecture)
  - [PostgreSQL Database Support](#postgresql-database-support)
- [Code Conventions and Style Guide](#code-conventions-and-style-guide)
  - [Naming Conventions](#naming-conventions)
  - [Code Style](#code-style)
- [Core Components](#core-components)
  - [Constants Module](#constants-module)
  - [API Integration](#api-integration)
- [Performance Optimization](#performance-optimization)
  - [Pre-computed Indices with fastmap](#pre-computed-indices-with-fastmap)
  - [O(1) OMIM Lookups](#o1-omim-lookups)
  - [Memoization](#memoization)
  - [Reactive Caching](#reactive-caching)
- [Testing](#testing)
  - [Testing Framework](#testing-framework)
  - [Running Tests](#running-tests)
  - [Writing Tests](#writing-tests)
- [Contributing](#contributing)
  - [Development Workflow](#development-workflow)
  - [Adding New Functions](#adding-new-functions)
- [API Reference Guide](#api-reference-guide)
  - [External APIs Used](#external-apis-used)
  - [Exported Functions Summary](#exported-functions-summary)

---

## Introduction

### Overview

The **maRco** package (version 0.11.0) is a specialized R package providing helper functions for the Small Vessel Disease (SVD) Dashboard application. It provides a comprehensive collection of utilities designed to support:

- Data cleaning and preprocessing
- External API integration (NCBI, UniProt, PubMed, OMIM)
- Shiny UI components and server logic
- Interactive data visualization
- Performance-optimized filtering systems

This documentation is intended for developers who wish to contribute to the package, understand its architecture, or extend its functionality.

### Package Philosophy

The maRco package follows several core design principles:

1. **Performance First**: Pre-computed indices, memoization, and data.table operations ensure responsive user interactions.
2. **Modularity**: Functions are organized by domain (data cleaning, API fetching, UI components) for maintainability.
3. **Defensive Programming**: All external API calls and data operations include error handling with informative messages.
4. **Documentation**: Every exported function includes comprehensive Roxygen2 documentation.

### Getting Started

#### Prerequisites

Before contributing to maRco, ensure you have:

- R version 4.1.0 or higher (required for native pipe operator `|>`)
- RStudio or another R IDE
- Git for version control
- `devtools` and `roxygen2` packages installed

#### Installation for Development

Clone the repository and install in development mode:

```bash
# Clone the repository
git clone <repository-url>
cd maRco

# Install development dependencies
install.packages(c("devtools", "roxygen2", "testthat"))

# Load package in development mode
devtools::load_all()

# Or install locally
devtools::install()
```

---

## Package Architecture

### Directory Structure

The package follows standard R package conventions:

```
maRco/
|-- DESCRIPTION          # Package metadata and dependencies
|-- NAMESPACE            # Export/import declarations (auto-generated)
|-- LICENSE              # MIT license
|-- README.md            # Package documentation
|-- R/                   # Source files (14 files, 4,756 lines total)
|   |-- server_helpers.R                 805
|   |-- tooltips.R                       738
|   |-- data_prep.R                      716
|   |-- utils.R                          396
|   |-- data_cleaning.R                  309
|   |-- filter_utils.R                   306
|   |-- phenogram.R                      269
|   |-- fetch_pubmed.R                   260
|   |-- fetch_uniprot.R                  233
|   |-- constants.R                      207
|   |-- fetch_ncbi.R                     179
|   |-- mod_checkbox_filter.R            167
|   |-- fetch_omim.R                      96
|   +-- maRco-package.R                   75
|-- man/                 # Documentation (117 .Rd files, auto-generated)
```

### Module Overview

| Module | Lines | Purpose |
|--------|-------|---------|
| `server_helpers.R` | 805 | Shiny server functions, filtering logic, DataTable builders |
| `tooltips.R` | 738 | HTML tooltip generation with Tippy.js integration |
| `data_prep.R` | 716 | Data loading pipeline, QS serialization, and fastmap indexing |
| `utils.R` | 396 | CSS styling utilities, database connections, column cleaning |
| `data_cleaning.R` | 309 | Raw data cleaning, PostgreSQL queries, and normalization |
| `filter_utils.R` | 306 | Unified filter utilities with `apply_column_filter()` |
| `phenogram.R` | 269 | Phenogram visualization for GWAS traits |
| `fetch_pubmed.R` | 260 | PubMed/Entrez reference fetching with connectivity checks |
| `fetch_uniprot.R` | 233 | UniProt REST API integration |
| `constants.R` | 207 | Configuration constants, mappings, and debounce settings |
| `fetch_ncbi.R` | 179 | NCBI Gene database queries |
| `mod_checkbox_filter.R` | 167 | Reusable Shiny filter modules |
| `fetch_omim.R` | 96 | OMIM disease link generation |
| `maRco-package.R` | 75 | Package-level documentation |
| **Total** | **4,756 lines** | |

### Data Flow Architecture

The package implements a pipeline architecture for data processing:

1. **Data Loading**: `load_and_prepare_data()` and `load_table2_data()` load RDS/QS files from disk or PostgreSQL database.
2. **Data Cleaning**: `clean_table1()` and `clean_table2()` parse and normalize raw data.
3. **Data Enrichment**: `fetch_*` functions add external data from NCBI, UniProt, PubMed, and OMIM.
4. **Display Preparation**: `prepare_table*_display()` converts data to HTML format with tooltips.
5. **Reactive Filtering**: `build_table*_filtered_data()` applies user-selected filters using `apply_column_filter()` from `filter_utils.R`.
6. **Rendering**: `build_table*_datatable()` creates interactive DataTable widgets.

### PostgreSQL Database Support

The package includes database connectivity via `RPostgres` for loading data directly from PostgreSQL:

```r
#' Execute Function with Database Connection
#' Manages database connection lifecycle with proper cleanup.
#'
#' @export
with_db_connection <- function(
  fn,
  con = NULL,
  dbname = "csvd_dashboard",
  host = "localhost",
  port = 5432,
  user = Sys.getenv("PGUSER"),
  password = Sys.getenv("PGPASSWORD")
) {
  close_con <- FALSE
  if (is.null(con)) {
    con <- DBI::dbConnect(
      RPostgres::Postgres(),
      dbname = dbname, host = host, port = port,
      user = user, password = password
    )
    close_con <- TRUE
  }
  on.exit(if (close_con) DBI::dbDisconnect(con))
  fn(con)
}
```

#### Environment Variables

Database credentials are configured via environment variables (typically in `.Renviron` or `.env`):

- `DB_USER`: PostgreSQL username
- `DB_PASSWORD`: PostgreSQL password
- `DB_HOST`: Database host (defaults to localhost)
- `DB_PORT`: Database port (defaults to 5432)
- `DB_NAME`: Database name (defaults to csvd_dashboard)

#### Usage in Data Cleaning

The `clean_table1()` and `clean_table2()` functions use `with_db_connection()` to query data:

```r
with_db_connection(function(con) {
  DBI::dbGetQuery(con, "SELECT * FROM genes")
})
```

---

## Code Conventions and Style Guide

### Naming Conventions

#### Functions

All functions use `snake_case` naming:

```r
# Good
clean_table1()
build_table1_datatable()
fetch_gene_info()

# Avoid
cleanTable1()
BuildTable1Datatable()
fetchGeneInfo()
```

Function names should follow these patterns:

- `clean_*`: Data cleaning operations
- `load_*`: Data loading from files
- `fetch_*`: External API calls
- `build_*`: Complex object construction
- `add_*`: Augmentation functions (e.g., tooltips)
- `apply_*`: Filter application functions
- `prepare_*`: Data transformation for display
- `extract_*`: Data extraction utilities
- `generate_*`: URL or content generation

#### Constants

Constants use `UPPER_SNAKE_CASE`:

```r
# Configuration
SAMPLE_SIZE_MIN <- 15
SAMPLE_SIZE_MAX <- 3156
HISTOGRAM_BREAKS <- 15
MEMO_CACHE_SIZE <- 50 * 1024^2  # 50 MB

# URLs
NCBI_GENE_BASE_URL <- "https://www.ncbi.nlm.nih.gov/gene/"
PUBMED_BASE_URL <- "https://pubmed.ncbi.nlm.nih.gov/"

# Placeholders
PLACEHOLDER_NONE_FOUND <- "(none found)"
PLACEHOLDER_UNKNOWN <- "(unknown)"
```

### Code Style

#### Pipe Operator

Use the native R pipe operator `|>` (R 4.1+):

```r
# Good - native pipe
result <- data |>
    filter(condition) |>
    select(columns) |>
    mutate(new_col = transformation)

# Avoid - magrittr pipe (unless using special features)
result <- data %>%
    filter(condition) %>%
    select(columns)
```

#### Error Handling

Always use `tryCatch` for external operations:

```r
safe_operation <- function(x) {
    tryCatch(
        {
            risky_operation(x)
        },
        error = function(e) {
            warning(paste("Operation failed:", e$message))
            return(NULL)  # or appropriate fallback
        }
    )
}
```

#### Documentation

Every exported function must include Roxygen2 documentation:

```r
#' Fetch Gene Information from NCBI
#'
#' Retrieves gene information from the NCBI Gene database
#' using the Entrez API.
#'
#' @param gene_symbol Character. The gene symbol to query.
#' @param delay Numeric. Delay between API calls in seconds.
#'
#' @return A data.frame with gene information, or NULL if
#'   the query fails.
#'
#' @export
fetch_gene_info <- function(gene_symbol, delay = 0.34) {
    # Implementation
}
```

---

## Core Components

### Constants Module

The `constants.R` file centralizes all configuration values.

#### Sample Size Configuration

```r
SAMPLE_SIZE_MIN <- 15
SAMPLE_SIZE_MAX <- 3156
HISTOGRAM_BREAKS <- 15
HISTOGRAM_XLIM_MAX <- 3500
HISTOGRAM_TICK_INTERVAL <- 500
```

#### Performance Tuning

```r
# Debounce delays (milliseconds)
SLIDER_DEBOUNCE_MS <- 500
CHECKBOX_DEBOUNCE_MS <- 150
DATATABLE_SEARCH_DELAY <- 750

# Cache configuration
MEMO_CACHE_SIZE <- 50 * 1024^2  # 50 MB
DATATABLE_PAGE_LENGTH <- 10
```

#### Domain Mappings

```r
# Cell type abbreviations
CELL_TYPE_MAP <- list(
    "EC" = "Endothelial Cells",
    "SMC" = "Smooth Muscle Cells",
    "VSMC" = "Vascular Smooth Muscle Cells",
    "AC" = "Astrocytes",
    "MG" = "Microglia",
    "OL" = "Oligodendrocytes",
    "PC" = "Pericytes",
    "FB" = "Fibroblasts"
)

# Omics technology names
OMICS_FULL_NAMES <- list(
    "PWAS" = "Proteome-Wide Association Study",
    "EWAS" = "Epigenome-Wide Association Study",
    "TWAS" = "Transcriptome-Wide Association Study"
)
```

### API Integration

#### NCBI Gene Fetching

```r
#' @export
fetch_gene_info <- function(gene_symbol, delay = 0.34) {
    Sys.sleep(delay)  # Rate limiting

    tryCatch({
        search_result <- rentrez::entrez_search(
            db = "gene",
            term = paste0(gene_symbol, "[Gene Name] AND Homo sapiens[Organism]"),
            retmax = 1
        )

        if (length(search_result$ids) == 0) {
            return(NULL)
        }

        gene_id <- search_result$ids[1]
        summary <- rentrez::entrez_summary(db = "gene", id = gene_id)

        data.frame(
            gene_symbol = gene_symbol,
            gene_id = gene_id,
            description = summary$description,
            aliases = paste(summary$otheraliases, collapse = ", ")
        )
    }, error = function(e) {
        warning(paste("Failed to fetch gene:", gene_symbol, "-", e$message))
        NULL
    })
}
```

#### UniProt Integration

```r
#' @export
fetch_uniprot_data <- function(accession) {
    base_url <- "https://rest.uniprot.org/uniprotkb/"

    tryCatch({
        response <- httr2::request(paste0(base_url, accession)) |>
            httr2::req_headers(Accept = "application/json") |>
            httr2::req_perform()

        data <- httr2::resp_body_json(response)

        list(
            accession = accession,
            protein_name = data$proteinDescription$recommendedName$fullName$value,
            go_terms = extract_go_terms(data)
        )
    }, error = function(e) {
        warning(paste("UniProt fetch failed:", accession))
        NULL
    })
}
```

---

## Performance Optimization

### Pre-computed Indices with fastmap

The `fastmap` package provides O(1) hash-based lookups:

```r
# During data loading
indices <- fastmap::fastmap()

# Pre-compute row indices for each filter value
for (trait in unique(data$gwas_trait)) {
    matching_rows <- which(data$gwas_trait == trait)
    indices$set(paste0("gwas_", trait), matching_rows)
}

# During filtering - O(1) lookup
get_filtered_rows <- function(selected_traits, indices) {
    all_rows <- integer(0)
    for (trait in selected_traits) {
        rows <- indices$get(paste0("gwas_", trait))
        all_rows <- union(all_rows, rows)
    }
    return(all_rows)
}
```

### O(1) OMIM Lookups

OMIM phenotype data is pre-computed into a fastmap for constant-time tooltip generation:

```r
# Create fastmap for O(1) OMIM lookups (pre-compute tooltip content)
omim_lookup <- fastmap::fastmap()
for (i in seq_len(nrow(omim_info))) {
  omim_num_val <- as.character(omim_info$omim_num[i])
  omim_lookup$set(
    omim_num_val,
    list(
      phenotype = omim_info$phenotype_clean[i],
      inheritance = omim_info$inheritance_clean[i],
      gene_or_locus = omim_info$gene_or_locus_clean[i],
      omim_link = as.character(omim_info$omim_link[i])
    )
  )
}
```

This replaces per-row `data.table` lookups with O(1) hash-based retrieval. For tables with thousands of OMIM references, this provides significant performance improvement during tooltip generation.

### Memoization

Cache expensive function results:

```r
library(memoise)
library(cachem)

# Create memoized version with size-limited cache
expensive_function_memo <- memoise(
    expensive_function,
    cache = cache_mem(max_size = MEMO_CACHE_SIZE)
)

# Cache is checked before computation
result <- expensive_function_memo(input)  # First call: computes
result <- expensive_function_memo(input)  # Second call: cache hit
```

### Reactive Caching

Use Shiny's `bindCache()` for reactive expressions:

```r
filtered_data <- reactive({
    data |>
        filter(trait %in% input$traits) |>
        filter(phase %in% input$phases)
}) |> bindCache(input$traits, input$phases)
```

---

## Testing

### Testing Framework

The package uses `testthat` (edition 3) for unit testing.

### Running Tests

```r
# Run all tests from the dashboard project directory
source("tests/test_all.R")

# Run tests with testthat framework
testthat::test_file("tests/test_all.R")

# Run tests with coverage
covr::file_coverage("tests/test_all.R")
```

### Writing Tests

#### Testing Constants

```r
test_that("sample size bounds are valid", {
    expect_true(SAMPLE_SIZE_MIN < SAMPLE_SIZE_MAX)
    expect_true(SAMPLE_SIZE_MIN >= 0)
    expect_true(is.numeric(SAMPLE_SIZE_MIN))
})

test_that("debounce values are reasonable", {
    expect_true(SLIDER_DEBOUNCE_MS > 0)
    expect_true(SLIDER_DEBOUNCE_MS < 2000)
})
```

---

## Contributing

### Development Workflow

#### Setting Up Your Environment

```bash
# Fork and clone the repository
git clone https://github.com/your-username/maRco.git
cd maRco

# Create a feature branch
git checkout -b feature/your-feature-name

# Install development dependencies
devtools::install_deps(dependencies = TRUE)
```

#### Development Cycle

1. Make changes to R files in the `R/` directory
2. Update Roxygen2 documentation
3. Run `devtools::document()` to regenerate NAMESPACE and man files
4. Run `devtools::check()` to verify package integrity
5. Run `devtools::test()` to execute tests
6. Run `lintr::lint_package()` to check style

### Adding New Functions

#### Checklist

- ✓ Function follows naming conventions (`snake_case`)
- ✓ Complete Roxygen2 documentation with `@export`
- ✓ Error handling with `tryCatch` where appropriate
- ✓ Unit tests in corresponding test file
- ✓ Constants defined in `constants.R` if needed

---

## API Reference Guide

### External APIs Used

#### NCBI Entrez API

- **Purpose**: Gene information retrieval
- **Package**: `rentrez`
- **Rate Limit**: 3 requests/second (without API key)
- **Documentation**: <https://www.ncbi.nlm.nih.gov/books/NBK25500/>

#### UniProt REST API

- **Purpose**: Protein data and Gene Ontology annotations
- **Base URL**: `https://rest.uniprot.org/uniprotkb/`
- **Rate Limit**: Generous, but respect 429 responses
- **Documentation**: <https://www.uniprot.org/help/api>

#### PubMed E-utilities

- **Purpose**: Reference citation retrieval
- **Package**: `rentrez`, `RefManageR`
- **Rate Limit**: 3 requests/second
- **Documentation**: <https://www.ncbi.nlm.nih.gov/books/NBK25499/>

### Exported Functions Summary

The package exports 102 functions and 27 constants. Key categories:

| Function/Constant | Description |
|-------------------|-------------|
| **Data Loading** | |
| `load_and_prepare_data()` | Main Table 1 data loading pipeline |
| `load_table2_data()` | Clinical trials data loading |
| `safe_read_rds()` | QS/RDS-aware file reading |
| `convert_rds_to_qs()` | Convert RDS files to QS format |
| **Data Cleaning** | |
| `clean_table1()` | Clean genetic targets table |
| `clean_table2()` | Clean clinical trials table |
| `with_db_connection()` | PostgreSQL connection management |
| **API Functions** | |
| `fetch_gene_info()` | Single NCBI gene lookup |
| `fetch_all_gene_info()` | Batch gene fetching |
| `fetch_uniprot_data()` | UniProt protein retrieval |
| `fetch_pub()` | Single PubMed reference (with connectivity check) |
| **Display Functions** | |
| `build_table1_datatable()` | Create Table 1 DataTable |
| `build_table2_datatable()` | Create Table 2 DataTable |
| `prepare_table1_display()` | Format Table 1 for display |
| `prepare_table2_display()` | Format Table 2 for display |
| **Constants** | |
| `SAMPLE_SIZE_MIN/MAX` | Sample size bounds |
| `MEMO_CACHE_SIZE` | Cache size limit |
| `CELL_TYPE_MAP` | Cell type mappings |
| `DATA_PATHS` | File path configuration |
