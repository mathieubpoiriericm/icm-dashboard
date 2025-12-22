# Cerebral SVD Dashboard

An interactive R Shiny dashboard for exploring putative causal genes and clinical trial drugs for Cerebral Small Vessel Disease (SVD), developed by Mathieu B. Poirier at the Paris Brain Institute (ICM).

## Overview

This dashboard provides up-to-date and standardized information on:
- Putative cerebral SVD causal genes
- Drugs tested in ongoing or completed cerebral SVD clinical trials

## Technology Stack

| Layer | Technologies |
|-------|-------------|
| Frontend | R Shiny, bslib (Bootstrap 5 with dark mode), DT, Plotly, Tippy.js |
| Backend | R 4.4+, data.table, fastmap, memoise, RPostgres |
| Data Pipeline | Python 3.13+, asyncpg, Biopython, Anthropic API |
| Database | PostgreSQL 18+ |
| DevOps | GitHub Actions, Docker, Kubernetes |

## Features

### Dark Mode
Toggle between light and dark themes using the built-in switch in the navbar. The dashboard features:
- Real-time theme switching powered by bslib's `input_dark_mode()`
- Glassmorphism effects in dark mode for a modern aesthetic
- Light theme: Clean white background with #2d287a primary accent
- Dark theme: #121212 background optimized for low-light viewing

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
Interactive Plotly timeline of SVD drugs tested in clinical trials.

## Installation

### Prerequisites

**For running the Shiny app only:**
- R (>= 4.4)
- The `maRco` helper package

**For running the data pipeline:**
- Python 3.13+
- PostgreSQL 18+

### Install R Dependencies

```r
# Install maRco package (required for data fetching/cleaning)
devtools::install("maRco")

# Install required CRAN packages
install.packages(c(
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
  "testthat",
  "qs",
  "parallel",
  "jsonlite",
  "shinyWidgets"
))

# Install Bioconductor packages
if (!require("BiocManager", quietly = TRUE))
  install.packages("BiocManager")
BiocManager::install(c("biomaRt", "UniprotR"))

# Optional packages for async data loading
install.packages(c("future", "promises"))
```

### Install Python Dependencies (for data pipeline)

```bash
pip install asyncpg biopython anthropic pydantic
```

### Database Setup (for data pipeline)

1. Install PostgreSQL 18
2. Create a database and user:
   ```sql
   CREATE USER csvd_user WITH PASSWORD 'your_password';
   CREATE DATABASE csvd_dashboard OWNER csvd_user;
   ```
3. Initialize the schema:
   ```bash
   psql -U csvd_user -d csvd_dashboard -f misc/sql_files/setup.sql
   ```

## Environment Variables

| Variable | Description | Required For |
|----------|-------------|--------------|
| `DB_HOST` | PostgreSQL host | Pipeline / live data |
| `DB_PORT` | PostgreSQL port (default: 5432) | Pipeline / live data |
| `DB_NAME` | Database name | Pipeline / live data |
| `PGPASSWORD` | Database password | Pipeline / live data |
| `ANTHROPIC_API_KEY` | Anthropic API key for LLM extraction | Pipeline only |
| `NCBI_API_KEY` | NCBI Entrez API key | Pipeline only |

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
├── app.R                     # Main application entry point
├── python_plot.py            # Clinical trials visualization generator
├── Dockerfile                # Docker container configuration
├── R/
│   ├── constants.R           # Application-wide constants
│   ├── utils.R               # CSS styles, DB utilities, column cleaning
│   ├── filter_utils.R        # Unified filter utilities for data.table
│   ├── data_prep.R           # Data loading and preprocessing
│   ├── tooltips.R            # Tooltip generation for tables
│   ├── mod_checkbox_filter.R # Shiny module for checkbox filters
│   ├── server.R              # Main server orchestrator
│   ├── server_table1.R       # Gene Table server logic
│   ├── server_table2.R       # Clinical Trials Table server logic
│   ├── ui.R                  # UI definition with Bootstrap 5
│   ├── clean_table1.R        # Table 1 data cleaning
│   ├── clean_table2.R        # Table 2 data cleaning
│   ├── fetch_ncbi_gene_data.R    # NCBI gene data fetching
│   ├── fetch_omim_data.R         # OMIM data fetching
│   ├── fetch_pubmed_data.R       # PubMed reference fetching
│   ├── fetch_uniprot_data.R      # UniProt protein data fetching
│   └── phenogram.R               # Phenogram data generation
├── pipeline/                 # Python data ETL
│   ├── pubmed_search.py      # PubMed literature search via Entrez
│   ├── pdf_retrieval.py      # PDF download module
│   ├── llm_extraction.py     # LLM-based data extraction (Anthropic)
│   ├── database.py           # PostgreSQL async operations
│   ├── data_merger.py        # Data consolidation utilities
│   ├── quality_metrics.py    # Data quality assessment
│   └── validation.py         # Data validation logic
├── scripts/                  # Database and utility scripts
│   ├── connection_pool.r     # R connection pooling
│   └── trigger_update.r      # Regenerate RDS from database
├── data/
│   ├── csv/                  # CSV data files
│   ├── rdata/                # Preprocessed data files (QS format)
│   ├── txt/                  # Text data files
│   └── xlsx/                 # Excel data files
├── www/
│   ├── custom.css            # Custom styles (source)
│   ├── custom.min.css        # Minified styles (loaded by app)
│   ├── custom.js             # Custom JavaScript (source)
│   ├── custom.min.js         # Minified JavaScript (loaded by app)
│   ├── python_plot.html      # Clinical trials visualization
│   ├── python_plot.js        # Plot interactivity and sidepanel
│   ├── phenogram_template.html  # Interactive phenogram
│   ├── fonts/                # Web fonts (Roboto)
│   ├── css/                  # Tippy.js styles
│   ├── js/                   # Popper.js and Tippy.js
│   └── images/               # Logo and phenogram images
├── tests/
│   └── testthat/             # Unit tests
│       ├── helper-setup.R
│       ├── test-constants.R
│       ├── test-data_prep.R
│       ├── test-server_modules.R
│       └── test-tooltips.R
├── maRco/                    # Helper functions R package
│   ├── R/                    # Package source files
│   ├── man/                  # Documentation
│   └── DESCRIPTION           # Package metadata
├── .github/workflows/
│   └── update_pipeline.yml   # Weekly automated pipeline
└── misc/                     # Supporting files
    ├── yaml_files/           # Kubernetes configurations
    ├── json_files/           # Grafana/monitoring configs
    ├── html_files/           # Documentation
    └── sql_files/            # Database schema
```

## Data Pipeline

The dashboard is powered by an automated data pipeline that keeps gene and clinical trial data up-to-date:

1. **Search**: Queries PubMed for recent SVD-related publications
2. **Retrieve**: Downloads PDFs and metadata via NCBI Entrez API
3. **Extract**: Uses Anthropic Claude to extract structured gene/drug/trial data
4. **Validate**: Performs data quality checks and validation
5. **Store**: Inserts/updates records in PostgreSQL database
6. **Export**: Regenerates RDS files for the Shiny application

### Running the Pipeline Manually

```bash
python pipeline/pubmed_search.py
```

### Automated Updates

The pipeline runs automatically every Monday at 6 AM UTC via GitHub Actions. See `.github/workflows/update_pipeline.yml` for configuration.

The workflow:
1. Spins up a PostgreSQL 18 service container
2. Initializes the database schema
3. Runs the Python pipeline
4. Regenerates RDS files using R
5. Auto-commits updated RDS files to the repository

## Deployment

### Local Development

```r
shiny::runApp()
```

### Docker

```bash
# Build the image
docker build -t svd-dashboard .

# Run the container
docker run -p 3838:3838 svd-dashboard
```

The app will be available at `http://localhost:3838`.

### Kubernetes

See `misc/yaml_files/` for Kubernetes deployment configurations including:
- Deployment manifests
- nginx ingress configuration
- Grafana monitoring integration

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

## Performance Features

### Startup Optimizations
- **CSS/JS Minification**: Source files are auto-minified at startup (37KB CSS → 12KB)
- **Disk Cache**: bslib Sass cache with 30-day TTL avoids recompilation
- **Local Fonts**: Roboto loaded from local files (faster than Google Fonts CDN)
- **Optimized Statistics**: Dashboard counts load minimal data, freeing memory immediately

### Runtime Optimizations
- **Fast Serialization**: QS format for data files (3-5x faster than RDS)
- **Fast Indexing**: fastmap for O(1) row lookups in filter operations
- **Data.table**: Efficient filtering with data.table instead of dplyr
- **Pre-computed Display**: Tooltips and display tables generated at startup, not runtime
- **Pre-computed Indices**: Filter row indices pre-computed for instant filter application

### Caching Strategies
- **In-Memory Caching**: memoise with 50MB cache for tooltips and computed values
- **Reactive Caching**: bindCache() prevents unnecessary recalculations
- **Lazy Loading**: Clinical trials table loads only when tab is accessed

### UI Responsiveness
- **Debounced Inputs**: Slider inputs (500ms) and checkbox filters (150ms) debounced
- **Async Loading**: Optional future/promises support for non-blocking data loads

## License

MIT License - see [LICENSE](https://opensource.org/licenses/MIT)

## Contact

**Maintenance**: mathieu.poirier@icm-institute.org

## Acknowledgments

Developed at the Paris Brain Institute (ICM).
