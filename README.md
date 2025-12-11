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
- GWAS traits (WMH, SVS, Stroke, Lacunes, etc.)
- Evidence from other omics studies (EWAS, TWAS, PWAS, Proteomics)

Includes linked data from:
- NCBI Gene
- UniProt
- OMIM
- PubMed references

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
  "data.table",
  "sysfonts",
  "showtext",
  "fastmap",
  "memoise",
  "cachem",
  "digest"
))
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
├── R/
│   ├── constants.R       # Application-wide constants
│   ├── utils.R           # CSS styles and utility functions
│   ├── data_prep.R       # Data loading and preprocessing
│   ├── tooltips.R        # Tooltip generation for tables
│   ├── mod_checkbox_filter.R  # Shiny module for checkbox filters
│   ├── server.R          # Main server logic
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
│   └── rdata/            # Preprocessed RData files
├── www/
│   ├── custom.css        # Custom styles
│   ├── custom.js         # Custom JavaScript
│   ├── fonts/            # Web fonts (Roboto)
│   ├── css/              # Tippy.js styles
│   ├── js/               # Popper.js and Tippy.js
│   └── images/           # Logo and phenogram images
└── misc/                 # Supporting files (bibentry, lintr reports)
```

## Data Sources

- **NCBI Gene**: Gene information and identifiers
- **UniProt**: Protein data
- **OMIM**: Online Mendelian Inheritance in Man
- **PubMed**: Publication references
- **Clinical Trial Registries**: ClinicalTrials.gov, ISRCTN, ANZCTR, ChiCTR

## License

MIT License - see [LICENSE](https://opensource.org/licenses/MIT)

## Contact

**Maintenance**: mathieu.poirier@icm-institute.org

## Acknowledgments

Developed at the Paris Brain Institute (ICM).
