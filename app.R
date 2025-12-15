# app.R
# SVD Dashboard - Main Application Entry Point
#
# This app has been modularized into the following structure:
# R/
#   ├── constants.R           - Application-wide constants
#   ├── utils.R               - Common CSS styles and utility functions
#   ├── data_prep.R           - Data loading and preprocessing functions
#   ├── tooltips.R            - Tooltip generation for table displays
#   ├── mod_checkbox_filter.R - Shiny module for checkbox filters
#   ├── server_table1.R       - Table 1 server logic
#   ├── server_table2.R       - Table 2 server logic
#   ├── server.R              - Main server logic
#   ├── ui.R                  - UI definition
#   ├── clean_table1.R        - Table 1 data cleaning
#   ├── clean_table2.R        - Table 2 data cleaning
#   ├── fetch_ncbi_gene_data.R    - NCBI gene data fetching
#   ├── fetch_omim_data.R         - OMIM data fetching
#   ├── fetch_pubmed_data.R       - PubMed reference fetching
#   ├── fetch_uniprot_data.R      - UniProt protein data fetching
#   └── phenogram.R               - Phenogram data generation
#
# Helper functions for data fetching/cleaning are in the maRco package.
# Install with: devtools::install("maRco")

# Load required packages
library(shiny)
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

# Load Roboto font from local file (faster than font_add_google)
font_add("Roboto", "www/fonts/Roboto-Regular.ttf")
showtext_auto()

# Source Shiny module files in dependency order
# Constants must be loaded first as other modules depend on them
source("R/constants.R")
source("R/utils.R")
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
  tooltip_style = tooltip_style,
  tooltip_style_italic = tooltip_style_italic
)

# Compute dashboard statistics (optimized to avoid loading full Table 2 data)
message("Computing dashboard statistics...")
n_genes <- length(unique(app_data$table1$Gene))
n_publications <- length(unique(unlist(app_data$table1$References)))

# Load only Table 2 for trial count and drug count (minimal data needed)
load("data/rdata/table2_clean.RData", envir = environment())
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

# Configure Shiny options for static asset caching
# max-age in seconds (86400 = 24 hours for static content)
options(shiny.staticimgcache = TRUE)

shinyApp(
  ui = ui,
  server = server,
  options = list(
    # Enable static resource caching
    shiny.autoreload = FALSE
  )
)
