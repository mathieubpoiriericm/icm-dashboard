# app.R
# SVD Dashboard - Main Application Entry Point
#
# Project Structure:
# ├── app.R                      - Main application entry point (this file)
# ├── python_plot.py             - Python visualization script
# ├── README.md                  - Project documentation
# │
# ├── R/                         - Shiny application modules
# │   ├── constants.R            - Application-wide constants
# │   ├── utils.R                - CSS styles, DB utils, column cleaning
# │   ├── filter_utils.R         - Unified filter and message rendering
# │   ├── data_prep.R            - Data loading and preprocessing functions
# │   ├── tooltips.R             - Tooltip generation for table displays
# │   ├── mod_checkbox_filter.R  - Shiny module for checkbox filters
# │   ├── server_table1.R        - Table 1 server logic
# │   ├── server_table2.R        - Table 2 server logic
# │   ├── server.R               - Main server logic
# │   ├── ui.R                   - UI definition
# │   ├── clean_table1.R         - Table 1 data cleaning
# │   ├── clean_table2.R         - Table 2 data cleaning
# │   ├── fetch_ncbi_gene_data.R - NCBI gene data fetching
# │   ├── fetch_omim_data.R      - OMIM data fetching
# │   ├── fetch_pubmed_data.R    - PubMed reference fetching
# │   ├── fetch_uniprot_data.R   - UniProt protein data fetching
# │   └── phenogram.R            - Phenogram data generation
# │
# ├── www/                       - Static web assets
# │   ├── custom.css             - Custom styles (source)
# │   ├── custom.min.css         - Minified CSS (generated)
# │   ├── custom.js              - Custom JavaScript (source)
# │   ├── custom.min.js          - Minified JS (generated)
# │   ├── python_plot.js         - Python plot integration JS (source)
# │   ├── python_plot.min.js     - Minified JS (generated)
# │   ├── python_plot.html       - Python plot template
# │   ├── phenogram_template.html - Phenogram HTML template
# │   ├── fonts/                 - Web fonts (Roboto)
# │   ├── images/                - Static images (logos, phenogram)
# │   ├── css/                   - Third-party CSS (tippy)
# │   └── js/                    - Third-party JS (popper, tippy)
# │
# ├── data/                      - Application data
# │   ├── csv/                   - CSV data files
# │   ├── qs/                    - QS data files (for faster loading)
# │   ├── txt/                   - Text data files
# │   └── xlsx/                  - Excel data files
# │
# ├── pipeline/                  - Automated data pipeline (Python)
# │   ├── main.py                - Pipeline orchestration entry point
# │   ├── pubmed_search.py       - PubMed literature search
# │   ├── pdf_retrieval.py       - PDF download module
# │   ├── llm_extraction.py      - LLM-based data extraction
# │   ├── validation.py          - Data validation logic
# │   ├── quality_metrics.py     - Quality metrics computation
# │   ├── database.py            - Database operations
# │   └── data_merger.py         - Data merging utilities
# │
# └── scripts/                   - Utility scripts
#     └── trigger_update.R       - Pipeline trigger script
#
# Helper functions for data fetching/cleaning are in the maRco package.
# Install with: devtools::install("maRco")

# Load required packages
library(shiny)
library(bslib)
# Load only required tidyverse packages (faster than full tidyverse)
library(dplyr)
library(purrr)
library(stringr)
library(DT)
library(tools)
library(data.table)
library(sysfonts)
library(showtext)
library(fastmap)
library(memoise)
library(cachem)
library(digest)
library(parallel)
library(qs)
library(jsonlite)
library(shinyWidgets)

# Optional: promises and future packages for async Table 2 loading
# Install with: install.packages(c("promises", "future"))
# Enable by setting ASYNC_TABLE2_LOADING <- TRUE in R/constants.R
async_packages_available <- requireNamespace("promises", quietly = TRUE) &&
  requireNamespace("future", quietly = TRUE)
if (async_packages_available) {
  message("promises/future packages available - async loading supported")
  future::plan(future::multisession, workers = 2L)
}

# Load Roboto font from local file (faster than font_add_google)
font_path <- "www/fonts/Roboto-Regular.ttf"
if (file.exists(font_path)) {
  font_add("Roboto", font_path)
} else {
  warning("Roboto font not found at ", font_path, ", using system default")
}
showtext_auto()

# Pre-cache bslib Sass compilation for faster startup
# bslib uses the sass package internally; setting a disk cache means
# compiled CSS is reused across app restarts instead of recompiling
message("Setting up Sass/bslib theme cache...")
bslib_cache_dir <- file.path(getwd(), ".bslib-cache")
if (!dir.exists(bslib_cache_dir)) {
  dir.create(bslib_cache_dir, recursive = TRUE)
}
options(
  sass.cache = cachem::cache_disk(
    dir = bslib_cache_dir,
    max_size = 50 * 1024^2,
    max_age = 60 * 60 * 24 * 30 # 30-day cache
  )
)

# Minify CSS for production performance
message("Minifying CSS...")
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
  # Trim

  css <- trimws(css)

  writeLines(css, output_path)
  minified_size <- nchar(css)

  list(original = original_size, minified = minified_size)
}

css_result <- tryCatch(
  {
    minify_css("www/custom.css", "www/custom.min.css")
  },
  error = function(e) {
    warning("CSS minification failed: ", e$message)
    NULL
  }
)

if (!is.null(css_result)) {
  # Guard against division by zero
  reduction <- if (css_result$original > 0L) {
    (1 - css_result$minified / css_result$original) * 100
  } else {
    0
  }
  message(sprintf(
    "  CSS minified: %s -> %s bytes (%.1f%% reduction)",
    format(css_result$original, big.mark = ","),
    format(css_result$minified, big.mark = ","),
    reduction
  ))
}

# Minify JavaScript for production performance
message("Minifying JavaScript...")
minify_js <- function(input_path, output_path) {
  js <- readLines(input_path, warn = FALSE) |> paste(collapse = "\n")
  original_size <- nchar(js)

  # Remove single-line comments (but not URLs with //)
  js <- gsub("(^|[^:])//.*?(?=\n|$)", "\\1", js, perl = TRUE)
  # Remove multi-line comments
  js <- gsub("/\\*[\\s\\S]*?\\*/", "", js, perl = TRUE)
  # Collapse whitespace (but preserve strings)
  js <- gsub("\\s+", " ", js, perl = TRUE)
  # Remove spaces around operators and braces

  js <- gsub("\\s*([{};:,=+\\-*/<>!&|?])\\s*", "\\1", js, perl = TRUE)
  # Trim
  js <- trimws(js)

  writeLines(js, output_path)
  minified_size <- nchar(js)

  list(original = original_size, minified = minified_size)
}

js_files <- list(
  list(input = "www/custom.js", output = "www/custom.min.js"),
  list(input = "www/python_plot.js", output = "www/python_plot.min.js")
)

for (js_file in js_files) {
  js_result <- tryCatch(
    {
      minify_js(js_file$input, js_file$output)
    },
    error = function(e) {
      warning("JS minification failed for ", js_file$input, ": ", e$message)
      NULL
    }
  )

  if (!is.null(js_result)) {
    # Guard against division by zero
    reduction <- if (js_result$original > 0L) {
      (1 - js_result$minified / js_result$original) * 100
    } else {
      0
    }
    message(sprintf(
      "  %s: %s -> %s bytes (%.1f%% reduction)",
      basename(js_file$output),
      format(js_result$original, big.mark = ","),
      format(js_result$minified, big.mark = ","),
      reduction
    ))
  }
}

# Source Shiny module files in dependency order
# Constants must be loaded first as other modules depend on them
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

# Load and prepare data at startup
message("Loading application data...")
app_data <- load_and_prepare_data()

# Pre-compute Table 1 display with tooltips
message("Preparing Table 1 display...")
table1_display <- prepare_table1_display(
  table1 = app_data$table1,
  gene_info_results_df = app_data$gene_info_results_df,
  prot_info_clean = app_data$prot_info_clean,
  omim_lookup = app_data$omim_lookup,
  refs = app_data$refs,
  omics_df = app_data$omics_df,
  gwas_trait_mapping = app_data$gwas_trait_mapping,
  tooltip_class = tooltip_class,
  tooltip_class_italic = tooltip_class_italic
)

# Compute dashboard statistics (optimized to avoid loading full Table 2 data)
message("Computing dashboard statistics...")
n_genes <- length(unique(app_data$table1$Gene))
n_publications <- length(unique(unlist(app_data$table1$References)))

# Load only Table 2 for trial count and drug count (minimal data needed)
table2 <- safe_read_data(DATA_PATHS$table2_clean)
n_trials <- length(unique(table2$`Registry ID`))
n_drugs <- length(unique(table2$Drug))
rm(table2) # Free memory immediately

message("Application ready!")

# Build and run the app
ui <- build_ui(
  n_genes = n_genes,
  n_drugs = n_drugs,
  n_trials = n_trials,
  n_pubs = n_publications
)
server <- build_server(app_data, table1_display)

# Configure Shiny options for static asset caching and performance
options(
  # Enable static image caching
  shiny.staticimgcache = TRUE,
  # Disable autoreload in production for stability
  shiny.autoreload = FALSE,
  # Enable full stack traces for debugging (disable in production if needed)
  shiny.fullstacktrace = FALSE,
  # Set larger upload limit if needed (in bytes, 30MB default)
  shiny.maxRequestSize = 30 * 1024^2
)

# Custom resource path with caching headers for static assets
shiny::addResourcePath(
  prefix = "static",
  directoryPath = file.path(getwd(), "www")
)

shinyApp(
  ui = ui,
  server = server,
  options = list(
    # Host binding for production deployment
    host = "0.0.0.0",
    # Launch browser only in interactive mode
    launch.browser = interactive()
  ),
  # Enable HTTP caching for static resources (requires shiny >= 1.7.0)
  # Static files cached by browsers for improved repeat visit performance
  enableBookmarking = NULL
)
