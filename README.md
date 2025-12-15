# Cerebral SVD Dashboard

An interactive R Shiny dashboard for exploring putative causal genes and clinical trial drugs for Cerebral Small Vessel Disease (SVD), developed by Mathieu B. Poirier at the Paris Brain Institute (ICM).

## Overview

This dashboard provides up-to-date and standardized information on:
- Putative cerebral SVD causal genes
- Drugs tested in ongoing or completed cerebral SVD clinical trials

## Features

### Gene Table
Browse putative causal genes with filters for:
- Mendelian Randomization status
- GWAS traits (SVS, BG-PVS, WMH, HIP-PVS, PSMD, extreme-cSVD, Lacunes, Stroke,
  NODDI, FA, MD, Lacunar Stroke, Small Vessel Stroke)
- Evidence from other omics studies (EWAS, TWAS, PWAS, Proteomics, MENTR)

Includes linked data from:
- NCBI Gene (with gene ID, protein, and aliases)
- UniProt (with accession numbers)
- OMIM (with phenotype, inheritance, gene/locus)
- PubMed references (with publication details)

### Clinical Trials Table
Explore drugs in clinical trials with filters for:
- Genetic evidence
- Trial registry (ClinicalTrials.gov, ISRCTN, ANZCTR, ChiCTR)
- Clinical trial phase (I, II, III)
- SVD population (CAA, Cognitive Impairment, Stroke, SVD)
- Target sample size
- Sponsor type (Academic, Industry)

### Phenogram
Interactive chromosome ideogram visualization of GWAS phenotypes.

### Clinical Trials Visualization
Interactive plot of SVD drugs tested in clinical trials.

## Installation

### Prerequisites
- R (>= 4.0)
- The `maRco` helper package

### Install Dependencies

```r
# Install maRco package (required for data fetching/cleaning)
devtools::install("maRco")

# Install required CRAN packages
install.packages(c(
  "shiny",
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
  "htmltools",
  "httr2",
  "xml2",
  "rlang",
  "readr",
  "tidyselect",
  "DBI",
  "RPostgres",
  "rentrez",
  "RefManageR",
  "rbibutils",
  "testthat"
))

# Install Bioconductor packages
if (!require("BiocManager", quietly = TRUE))
  install.packages("BiocManager")
BiocManager::install(c("biomaRt", "UniprotR"))
```

## Usage

Run the application:

```r
shiny::runApp()
```

Or from the command line:

```bash
Rscript -e "shiny::runApp()"
```

## Project Structure

```
rshiny_dashboard/
├── app.R                 # Main application entry point
├── python_plot.py        # Clinical trials visualization generator
├── R/
│   ├── constants.R       # Application-wide constants
│   ├── utils.R           # CSS styles, DB utilities, column cleaning
│   ├── filter_utils.R    # Unified filter utilities for data.table
│   ├── data_prep.R       # Data loading and preprocessing
│   ├── tooltips.R        # Tooltip generation for tables
│   ├── mod_checkbox_filter.R  # Shiny module for checkbox filters
│   ├── server.R          # Main server orchestrator
│   ├── server_table1.R   # Gene Table server logic
│   ├── server_table2.R   # Clinical Trials Table server logic
│   ├── ui.R              # UI definition
│   ├── clean_table1.R    # Table 1 data cleaning
│   ├── clean_table2.R    # Table 2 data cleaning
│   ├── fetch_ncbi_gene_data.R    # NCBI gene data fetching
│   ├── fetch_omim_data.R         # OMIM data fetching
│   ├── fetch_pubmed_data.R       # PubMed reference fetching
│   ├── fetch_uniprot_data.R      # UniProt protein data fetching
│   └── phenogram.R               # Phenogram data generation
├── data/
│   ├── csv/              # CSV data files
│   ├── rdata/            # Preprocessed RData files
│   ├── txt/              # Text data files
│   └── xlsx/             # Excel data files
├── www/
│   ├── custom.css        # Custom styles (source)
│   ├── custom.min.css    # Minified styles (loaded by app)
│   ├── custom.js         # Custom JavaScript (source)
│   ├── custom.min.js     # Minified JavaScript (loaded by app)
│   ├── python_plot.html  # Clinical trials visualization
│   ├── python_plot.js    # Plot interactivity and sidepanel
│   ├── phenogram_template.html  # Interactive phenogram
│   ├── fonts/            # Web fonts (Roboto)
│   ├── css/              # Tippy.js styles
│   ├── js/               # Popper.js and Tippy.js
│   └── images/           # Logo and phenogram images
├── tests/
│   └── testthat/         # Unit tests
│       ├── helper-setup.R
│       ├── test-constants.R
│       ├── test-data_prep.R
│       ├── test-server_modules.R
│       └── test-tooltips.R
├── maRco/                # Helper functions R package
│   ├── R/                # Package source files
│   ├── man/              # Documentation
│   └── DESCRIPTION       # Package metadata
└── misc/                 # Supporting files (documentation, lintr)
```

## Data Sources

- **NCBI Gene**: Gene information and identifiers
- **UniProt**: Protein data and Gene Ontology annotations
- **OMIM**: Online Mendelian Inheritance in Man
- **PubMed**: Publication references
- **Clinical Trial Registries**: ClinicalTrials.gov, ISRCTN, ANZCTR, ChiCTR

## Testing

Run unit tests with:

```r
testthat::test_dir("tests/testthat")
```

## Clinical Trials Visualization

The clinical trials timeline plot is generated by `python_plot.py` using Plotly.
To regenerate the visualization:

```bash
python python_plot.py
```

This creates `www/python_plot.html` and `www/python_plot.js`.

## License

MIT License - see [LICENSE](https://opensource.org/licenses/MIT)

## Contact

**Maintenance**: mathieu.poirier@icm-institute.org

## Acknowledgments

Developed at the Paris Brain Institute (ICM).
