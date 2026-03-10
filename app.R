# app.R
# SVD Dashboard - Main Application Entry Point
#
# Enable detailed error reporting for debugging
# Set BEFORE loading any packages to catch early failures
#
options(
  shiny.sanitize.errors = TRUE,
  shiny.fullstacktrace = TRUE,
  warn = 1 # Print warnings as they occur
)
#
# Helper functions are also available in the maRco package.
# Install with: devtools::install("maRco")

# Load required packages with error handling
message("Loading R packages...")

# ============================================================================
# PACKAGE DEPENDENCIES
# These explicit library() calls are required for rsconnect to detect
# dependencies during deployment. The actual loading with error handling
# happens below.
# ============================================================================
if (FALSE) {
  library(shiny)
  library(bslib)
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
  library(leaflet)
  library(tidygeocoder)
  library(htmltools)
}

required_packages <- c(
  "shiny",
  "bslib",
  "dplyr",
  "purrr",
  "stringr",
  "DT",
  "tools",
  "data.table",
  "sysfonts",
  "showtext",
  "fastmap",
  "memoise",
  "cachem",
  "digest",
  "parallel",
  "qs",
  "jsonlite",
  "shinyWidgets",
  "leaflet",
  "tidygeocoder",
  "htmltools"
)

load_package <- function(pkg) {
  tryCatch(
    {
      library(pkg, character.only = TRUE)
      message(sprintf("  Loaded: %s", pkg))
      TRUE
    },
    error = function(e) {
      message(sprintf("  FAILED to load %s: %s", pkg, e$message))
      FALSE
    }
  )
}

load_results <- sapply(required_packages, load_package)
failed_packages <- names(load_results[!load_results])

if (length(failed_packages) > 0) {
  stop(
    sprintf(
      "Failed to load required packages: %s\n%s",
      paste(failed_packages, collapse = ", "),
      "Run 'install.packages()' to install missing packages."
    ),
    call. = FALSE
  )
}

message("All packages loaded successfully.")

# Load Inter font from local file (faster than font_add_google)
font_path <- "www/fonts/Inter-Regular.ttf"
font_path_bold <- "www/fonts/Inter-Bold.ttf"
if (file.exists(font_path) && file.exists(font_path_bold)) {
  font_add("Inter", regular = font_path, bold = font_path_bold)
} else {
  warning("Inter font files not found, using system default")
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
source("R/fetch_trial_locations.R")
source("R/server_map.R")
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
  prot_info_lookup = app_data$prot_info_lookup,
  omim_lookup = app_data$omim_lookup,
  refs = app_data$refs,
  omics_df = app_data$omics_df,
  gwas_trait_mapping = app_data$gwas_trait_mapping,
  tooltip_class = tooltip_class,
  tooltip_class_italic = tooltip_class_italic
)

# Compute dashboard statistics
message("Computing dashboard statistics...")
n_genes <- length(unique(app_data$table1$Gene))
n_publications <- length(unique(unlist(app_data$table1$References)))

# Preload Table 2 data at startup to eliminate delay on tab switch
if (PRELOAD_TABLE2) {
  message("Preloading Table 2 data...")
  table2_data <- load_table2_data()

  message("Preparing Table 2 display...")
  table2_display <- prepare_table2_display(
    table2_data$table2,
    table2_data$ct_info,
    table2_data$gene_info_table2,
    table2_data$gene_symbols_table2,
    tooltip_class = tooltip_class,
    tooltip_class_italic = tooltip_class_italic
  )

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

  n_trials <- length(unique(table2_data$table2$`Registry ID`))
  n_drugs <- length(unique(table2_data$table2$Drug))
  rm(table2_data) # Free intermediate data
} else {
  # Lazy loading mode - just get counts
  table2 <- safe_read_data(DATA_PATHS$table2_clean)
  n_trials <- length(unique(table2$`Registry ID`))
  n_drugs <- length(unique(table2$Drug))
  rm(table2)
  preloaded_table2 <- NULL
}

message("Application ready!")

# Build and run the app
ui <- build_ui(
  n_genes = n_genes,
  n_drugs = n_drugs,
  n_trials = n_trials,
  n_pubs = n_publications
)
server <- build_server(app_data, table1_display, preloaded_table2)

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
