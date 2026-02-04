# Cerebral SVD Dashboard

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Maintained](https://img.shields.io/badge/Maintained-yes-green.svg)](mailto:mathieu.poirier@icm-institute.org)
[![R Version](https://img.shields.io/badge/R-4.5+-blue.svg)](https://cran.r-project.org/)
[![Shiny](https://img.shields.io/badge/Shiny-Framework-blue.svg)](https://shiny.rstudio.com/)
[![Python](https://img.shields.io/badge/Python-3.14+-yellow.svg)](https://www.python.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-18+-purple.svg)](https://www.postgresql.org/)

An interactive R Shiny dashboard for exploring putative causal genes and clinical trial drugs for Cerebral Small Vessel Disease (SVD), developed by Mathieu B. Poirier at the Paris Brain Institute (ICM).

---

## Table of Contents

- [Overview](#overview)
- [Documentation](#documentation)
- [Quick Start](#quick-start)
- [Technology Stack](#technology-stack)
- [Features](#features)
- [Installation](#installation)
- [Environment Variables](#environment-variables)
- [Usage](#usage)
- [Project Structure](#project-structure)
- [Data Pipeline](#data-pipeline)
- [Deployment](#deployment)
- [Data Sources](#data-sources)
- [Testing](#testing)
- [Clinical Trials Visualization](#clinical-trials-visualization)
- [Performance Features](#performance-features)
- [Contributing](#contributing)
- [License](#license)
- [Contact](#contact)
- [Acknowledgments](#acknowledgments)

---

## Overview

This dashboard provides up-to-date and standardized information on:
- Putative cerebral SVD causal genes
- Drugs tested in ongoing or completed cerebral SVD clinical trials

---

## Documentation

Detailed documentation is available in the `docs/` directory:

| Document | Description |
|----------|-------------|
| [Dashboard Technical Documentation](docs/dashboard-technical-documentation.md) | Complete technical reference for the Shiny dashboard architecture |
| [maRco Package Documentation](docs/marco-package-documentation.md) | Developer guide for the maRco R helper package |
| [Automated Research Pipeline](docs/automated-research-pipeline.md) | Python ETL pipeline for gene extraction from literature |
| [Observability Stack Guide](docs/observability-stack-guide.md) | Kubernetes monitoring and logging setup |

---

## Quick Start

```bash
# 1. Clone the repository
git clone https://github.com/mathieubpoiriericm/icm-dashboard.git
cd icm-dashboard

# 2. Install the maRco helper package
Rscript -e 'devtools::install("maRco")'

# 3. Install dependencies (see Installation for full list)
Rscript -e 'install.packages(c("shiny", "bslib", "DT", "data.table", "qs"))'

# 4. Run the app
Rscript -e 'shiny::runApp()'
```

The dashboard will open in your browser at `http://127.0.0.1:3838`.

---

## Technology Stack

| Layer | Technologies |
|-------|-------------|
| Frontend | R Shiny, bslib (Bootstrap 5 with dark mode), DT, Leaflet, Tippy.js |
| Visualization | Plotly (Python-generated timeline) |
| Backend | R 4.5+, data.table, fastmap, memoise, RPostgres |
| Data Pipeline | Python 3.14+, asyncpg, Biopython, Anthropic API |
| Database | PostgreSQL 18+ |
| DevOps | GitHub Actions, Docker, Kubernetes |

---

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

### Clinical Trials Map
Interactive Leaflet map displaying global research sites for NCT-registered trials:
- Fetches trial locations from ClinicalTrials.gov API v2
- Geocodes locations using OpenStreetMap Nominatim (via tidygeocoder)
- Marker clustering for improved performance at low zoom levels
- Rich HTML popups with trial metadata (drug, phase, sponsor, status, sample size)
- Color-coded status badges (recruiting, active, completed, terminated)
- Direct links to ClinicalTrials.gov trial pages
- Lazy loading (data fetched only when tab accessed)
- Cached geocoded data with SHA256 integrity verification

---

## Installation

### Prerequisites

**For running the Shiny app only:**
- R 4.5+
- The `maRco` helper package

**For running the data pipeline:**
- Python 3.14+
- PostgreSQL 18+

### Install R Dependencies

```r
# Install maRco package (v0.12.1 - required for data fetching/cleaning)
devtools::install("maRco")
```

<details>
<summary><strong>Click to expand full R package list</strong></summary>

```r
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
  "shinytest2",
  "qs",
  "parallel",
  "jsonlite",
  "shinyWidgets",
  "leaflet",
  "tidygeocoder",
  "future",
  "future.apply"
))

# Install Bioconductor packages
if (!require("BiocManager", quietly = TRUE))
  install.packages("BiocManager")
BiocManager::install(c("biomaRt", "UniprotR"))
```

</details>

### Install Python Dependencies (for data pipeline)

```bash
pip install -r requirements.txt
```

<details>
<summary><strong>Click to expand Python package list</strong></summary>

```txt
# HTTP client (async)
httpx>=0.24.0

# XML parsing
lxml>=4.9.0

# LLM API
anthropic>=0.25.0

# Data validation
pydantic>=2.0.0

# Bioinformatics
biopython>=1.81

# Database
asyncpg>=0.28.0

# Environment variables
python-dotenv>=1.0.0

# PDF extraction
PyMuPDF>=1.23.0
```

</details>

<details>
<summary><strong>Click to expand database setup instructions</strong></summary>

### Database Setup (for data pipeline)

1. Install PostgreSQL 18
2. Create a database and user:
   ```sql
   CREATE USER csvd_user WITH PASSWORD 'your_password';
   CREATE DATABASE csvd_dashboard OWNER csvd_user;
   ```
3. Initialize the schema:
   ```bash
   psql -U csvd_user -d csvd_dashboard -f sql/setup.sql
   ```

</details>

---

## Environment Variables

<details>
<summary><strong>Click to expand environment variables table</strong></summary>

| Variable | Description | Required For |
|----------|-------------|--------------|
| `DB_HOST` | PostgreSQL host | Pipeline / live data |
| `DB_PORT` | PostgreSQL port (default: 5432) | Pipeline / live data |
| `DB_NAME` | Database name | Pipeline / live data |
| `DB_USER` | Database username | Pipeline / live data |
| `DB_PASSWORD` | Database password | Pipeline / live data |
| `ANTHROPIC_API_KEY` | Anthropic API key for LLM extraction | Pipeline only |
| `NCBI_API_KEY` | NCBI Entrez API key | Pipeline only |

</details>

---

## Usage

Run the application:

```r
shiny::runApp()
```

Or from the command line:

```bash
Rscript -e "shiny::runApp()"
```

---

## Project Structure

<details>
<summary><strong>Click to expand project structure</strong></summary>

```
rshiny_dashboard/
├── app.R                         # Main application entry point
├── python_plot.py                # Clinical trials visualization generator
├── Dockerfile                    # Docker build configuration
├── LICENSE                       # MIT License
├── README.md                     # Project documentation
├── bibentry/                     # PubMed reference files (XML/BIB)
├── data/
│   ├── csv/                      # CSV source files (table1.csv, table2.csv, etc.)
│   ├── qs/                       # QS serialized data for Shiny app
│   ├── txt/                      # Text data files
│   └── xlsx/                     # Excel source files
├── logs/                         # Pipeline execution logs
├── makefile/
│   └── Makevars                  # R compilation flags
├── monitoring/
│   ├── json/                     # Grafana dashboard configuration
│   └── yaml/                     # Kubernetes monitoring configs
│       ├── monitoring-stack.yaml # Monitoring stack deployment
│       └── victorialogs-values.yaml  # VictoriaLogs configuration
├── published_dashboard_preview/  # ShinyApps.io deployment preview
├── R/
│   ├── constants.R               # Application-wide constants
│   ├── utils.R                   # CSS styles, DB utilities, column cleaning
│   ├── filter_utils.R            # Unified filter utilities for data.table
│   ├── data_prep.R               # Data loading and preprocessing
│   ├── tooltips.R                # Tooltip generation for tables
│   ├── mod_checkbox_filter.R     # Shiny module for checkbox filters
│   ├── server.R                  # Main server orchestrator
│   ├── server_table1.R           # Gene Table server logic
│   ├── server_table2.R           # Clinical Trials Table server logic
│   ├── ui.R                      # UI definition with Bootstrap 5
│   ├── clean_table1.R            # Table 1 data cleaning
│   ├── clean_table2.R            # Table 2 data cleaning
│   ├── fetch_ncbi_gene_data.R    # NCBI gene data fetching
│   ├── fetch_omim_data.R         # OMIM data fetching
│   ├── fetch_pubmed_data.R       # PubMed reference fetching
│   ├── fetch_uniprot_data.R      # UniProt protein data fetching
│   ├── read_external_data.R      # External data reading utilities
│   ├── phenogram.R               # Phenogram data generation
│   ├── fetch_trial_locations.R   # Trial location fetching and geocoding
│   └── server_map.R              # Clinical Trials Map server logic
├── pipeline/
│   ├── main.py                   # CLI entry point & pipeline orchestrator
│   ├── pubmed_search.py          # PubMed literature search via Entrez
│   ├── pdf_retrieval.py          # Multi-source text retrieval (PMC/Unpaywall/Abstract)
│   ├── llm_extraction.py         # LLM-based gene extraction (Anthropic Claude)
│   ├── validation.py             # NCBI gene verification & confidence filtering
│   ├── data_merger.py            # Data transformation & database loading
│   ├── database.py               # Async PostgreSQL operations
│   ├── quality_metrics.py        # Pipeline statistics tracking
│   ├── ncbi_gene_fetch.py        # NCBI Gene data fetching
│   ├── pubmed_citations.py       # PubMed citation handling
│   ├── uniprot_fetch.py          # UniProt data fetching
│   └── external_data_sync.py     # External data synchronization
├── sql/
│   ├── setup.sql                 # Database schema initialization
│   ├── add_external_data_tables.sql  # External data table schemas
│   └── common_queries.sql        # Frequently used SQL queries
├── scripts/
│   └── trigger_update.R          # Regenerate QS files from database
└── www/
    ├── custom.css                # Custom styles (source)
    ├── custom.min.css            # Minified styles (loaded by app)
    ├── custom.js                 # Custom JavaScript (source)
    ├── custom.min.js             # Minified JavaScript (loaded by app)
    ├── python_plot.html          # Clinical trials visualization
    ├── python_plot.js            # Plot interactivity and sidepanel
    ├── python_plot.min.js        # Minified plot JavaScript
    ├── phenogram_template.html   # Interactive phenogram viewer
    ├── fonts/                    # Web fonts (Roboto)
    ├── css/                      # Tippy.js styles
    ├── js/                       # Popper.js and Tippy.js
    └── images/                   # Logo and phenogram images
```

</details>

---

## Data Pipeline

The dashboard uses a two-stage data pipeline: a Python ETL pipeline that extracts gene data from PubMed literature, followed by an R script that transforms the data into optimized QS files for the Shiny app.

### Pipeline Architecture

```
Stage 1: Python ETL                           Stage 2: R Transformation
┌──────────────────────────────────────┐      ┌─────────────────────────┐
│ 1. Search PubMed for new papers      │      │ 7. Read from PostgreSQL │
│ 2. Filter already-processed PMIDs    │      │ 8. Generate QS files    │
│ 3. Retrieve full text (PMC/Unpaywall)│      │    for Shiny dashboard  │
│ 4. Extract genes via Claude LLM      │      └───────────▲─────────────┘
│ 5. Validate against NCBI Gene        │                  │
│ 6. Load into PostgreSQL              │      ┌───────────┴─────────────┐
│              │                       │      │   trigger_update.R      │
│              ▼                       │      └───────────▲─────────────┘
│ 6a. Sync external data (optional):   │                  │
│     NCBI Gene, UniProt, PubMed refs  │──────────────────┘
└──────────────────────────────────────┘
```

### Running the Pipeline

**Stage 1: Python ETL**

```bash
# Standard run (search last 7 days, extract genes)
python pipeline/main.py

# Sync external data (NCBI Gene, UniProt, PubMed citations)
python pipeline/main.py --sync-external-data

# Extended lookback (30 days)
python pipeline/main.py --days-back 30
```

| Argument | Default | Description |
|----------|---------|-------------|
| `--days-back` | 7 | Number of days to look back for new papers (1-3650) |
| `--dry-run` | - | Run pipeline without writing to database |
| `--test-mode` | - | Skip LLM extraction (test search/retrieval only) |
| `--sync-external-data` | - | Sync NCBI Gene, UniProt, and PubMed citation data |

**Stage 2: R Transformation**

```bash
Rscript scripts/trigger_update.R
```

Reads from PostgreSQL and generates QS files for the Shiny app:

| QS File | Source | Description |
|---------|--------|-------------|
| `table1_clean.qs` | `genes` table | Cleaned gene data for Table 1 |
| `table2_clean.qs` | `clinical_trials` table | Cleaned clinical trials for Table 2 |
| `gene_info_results_df.qs` | `ncbi_gene_info` cache | NCBI Gene info for Table 1 |
| `gene_info_table2.qs` | `ncbi_gene_info` cache | NCBI Gene info for Table 2 |
| `prot_info_clean.qs` | `uniprot_info` cache | UniProt protein annotations |
| `refs.qs` | `pubmed_citations` cache | Formatted PubMed references |
| `gwas_trait_names.qs` | `genes` table | GWAS trait name mappings |
| `geocoded_trials.qs` | ClinicalTrials.gov API | Geocoded trial locations for map |

### Automated Updates

> **TODO**: Automated pipeline updates via GitHub Actions are planned but not currently implemented. The previous workflow (`.github/workflows/update_pipeline.yml`) has been removed. For now, run the pipeline manually using the commands above.

**Planned Workflow Steps:**

1. Initialize PostgreSQL 18 service container and load schema
2. Run Python pipeline (`main.py`) to extract new genes
3. Generate QS files via `trigger_update.R`
4. Auto-commit updated `data/qs/*.qs` files to the repository

The QS files are committed directly to the repository, allowing the Shiny app to run without database access in production.

---

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

See `monitoring/yaml/` for Kubernetes deployment configurations including:
- Monitoring stack deployment
- VictoriaLogs configuration
- Grafana monitoring integration

---

## Data Sources

- **NCBI Gene**: Gene information and identifiers
- **UniProt**: Protein data and Gene Ontology annotations
- **OMIM**: Online Mendelian Inheritance in Man
- **PubMed**: Publication references
- **Clinical Trial Registries**: ClinicalTrials.gov, ISRCTN, ANZCTR, ChiCTR
- **ClinicalTrials.gov API v2**: Trial locations and metadata for the Clinical Trials Map
- **OpenStreetMap Nominatim**: Location geocoding via tidygeocoder

---

## Testing

Run unit tests with:

```r
source("tests/test_all.R")
```

---

## Clinical Trials Visualization

The clinical trials timeline plot is generated by `python_plot.py` using Plotly.
To regenerate the visualization:

```bash
python python_plot.py
```

This creates `www/python_plot.html` and `www/python_plot.js`.

---

## Performance Features

### Startup Optimizations
- **CSS/JS Minification**: Source files are auto-minified at startup (37KB CSS → 12KB)
- **Disk Cache**: bslib Sass cache with 30-day TTL avoids recompilation
- **Local Fonts**: Roboto loaded from local files (faster than Google Fonts CDN)
- **Optimized Statistics**: Dashboard counts load minimal data, freeing memory immediately
- **Vectorized Index Building**: GWAS trait and omics type indices built using `data.table::rbindlist()` + `split()` instead of row-by-row `vapply` loops

### Runtime Optimizations
- **Fast Serialization**: QS format for data files (3-5x faster than RDS)
- **Fast Indexing**: fastmap for O(1) row lookups in filter operations
- **O(1) OMIM Lookups**: `omim_lookup` fastmap for constant-time OMIM data retrieval
- **Data.table**: Efficient filtering with data.table instead of dplyr
- **Pre-computed Display**: Tooltips and display tables generated at startup, not runtime
- **Pre-computed Indices**: Filter row indices pre-computed for instant filter application
- **Direct Data References**: Preloaded Table 2 data uses direct reference instead of per-session copies (~1-3MB savings per session)
- **Session Memory Cleanup**: Explicit `session$onSessionEnded` handler clears per-session data in multi-user deployments
- **API Rate Limiting**: UniProt API requests throttled with 100ms delay to avoid rate limiting (configurable via `delay` parameter)

### Caching Strategies
- **In-Memory Caching**: memoise with 50MB cache for tooltips and computed values
- **Reactive Caching**: bindCache() prevents unnecessary recalculations
- **Preloaded Data**: Clinical trials table preloaded at startup for instant tab switching
- **Cached Plots**: Sample size histogram uses `renderCachedPlot()` with `sizeGrowthRatio()` for size-responsive caching

### UI Responsiveness
- **Debounced Inputs**: Slider inputs (500ms) and checkbox filters (150ms) debounced
- **Lazy-Loaded Iframes**: Phenogram and clinical trials visualizations use browser-native `loading="lazy"` attribute

### Clinical Trials Map Optimizations
- **Lazy Loading**: Map data fetched only when Clinical Trials Map tab is first accessed
- **Parallel API Requests**: Uses future/future.apply for concurrent ClinicalTrials.gov API calls
- **Rate Limiting**: Configurable delay between API request batches (100ms default) to avoid rate limiting
- **Geocoding Deduplication**: Unique locations geocoded once, then merged back to all markers
- **Cache with Integrity Verification**: Geocoded data cached to QS file with SHA256 hash verification
- **Marker Clustering**: Leaflet marker clusters improve rendering performance at low zoom levels
- **Incremental Updates**: leafletProxy used to update markers without re-rendering entire map
- **Exponential Backoff**: API failures retry with exponential backoff (1s, 2s, 4s)

---

## Contributing

Contributions are welcome! Here's how you can help:

1. **Fork** the repository
2. **Create** a feature branch (`git checkout -b feature/amazing-feature`)
3. **Commit** your changes (`git commit -m 'Add amazing feature'`)
4. **Push** to the branch (`git push origin feature/amazing-feature`)
5. **Open** a Pull Request

### Guidelines

- Follow the existing code style and conventions
- Add tests for new functionality
- Update documentation as needed
- Keep commits focused and atomic

### Reporting Issues

Found a bug or have a suggestion? Please [open an issue](https://github.com/mathieubpoiriericm/icm-dashboard/issues) with:
- A clear description of the problem or enhancement
- Steps to reproduce (for bugs)
- Expected vs actual behavior

---

## License

MIT License - see [LICENSE](https://opensource.org/licenses/MIT)

---

## Contact

**Maintenance**: mathieu.poirier@icm-institute.org

---

## Acknowledgments

Developed at the Paris Brain Institute (ICM).
