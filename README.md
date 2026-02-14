# Cerebral SVD Dashboard

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Maintained](https://img.shields.io/badge/Maintained-yes-green.svg)](mailto:mathieu.poirier@icm-institute.org)
[![R Version](https://img.shields.io/badge/R-4.5+-blue.svg)](https://cran.r-project.org/)
[![Shiny](https://img.shields.io/badge/Shiny-Framework-blue.svg)](https://shiny.rstudio.com/)
[![Python](https://img.shields.io/badge/Python-3.14+-yellow.svg)](https://www.python.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-18+-purple.svg)](https://www.postgresql.org/)

[![R Tests: 119](https://img.shields.io/badge/R_Tests-119_passing-green.svg)](#testing)
[![filter_utils](https://img.shields.io/badge/filter__utils-37_tests-brightgreen.svg)](#testing)
[![data_prep](https://img.shields.io/badge/data__prep-30_tests-brightgreen.svg)](#testing)
[![utils](https://img.shields.io/badge/utils-18_tests-brightgreen.svg)](#testing)
[![tooltips](https://img.shields.io/badge/tooltips-15_tests-brightgreen.svg)](#testing)
[![Edge Cases](https://img.shields.io/badge/Edge_Cases-14_tests-brightgreen.svg)](#testing)
[![shinytest2](https://img.shields.io/badge/shinytest2-5_tests-brightgreen.svg)](#testing)

[![Pipeline Tests: 290](https://img.shields.io/badge/Pipeline_Tests-290_passing-green.svg)](#testing)
[![Infrastructure](https://img.shields.io/badge/Infrastructure-85_tests-brightgreen.svg)](#testing)
[![LLM Extraction](https://img.shields.io/badge/LLM_Extraction-77_tests-brightgreen.svg)](#testing)
[![Data Processing](https://img.shields.io/badge/Data_Processing-59_tests-brightgreen.svg)](#testing)
[![Paper Retrieval](https://img.shields.io/badge/Paper_Retrieval-46_tests-brightgreen.svg)](#testing)
[![Orchestration](https://img.shields.io/badge/Orchestration-23_tests-brightgreen.svg)](#testing)

An interactive R Shiny dashboard for exploring putative causal genes and clinical trial drugs for Cerebral Small Vessel Disease (SVD), developed by Mathieu B. Poirier at the Paris Brain Institute (ICM).

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Quick Start](#quick-start)
- [Installation](#installation)
- [Environment Variables](#environment-variables)
- [Usage](#usage)
- [Deployment](#deployment)
- [Technology Stack](#technology-stack)
- [Project Structure](#project-structure)
- [Data Pipeline](#data-pipeline)
- [LLM Configuration](#llm-configuration)
- [Data Sources](#data-sources)
- [Clinical Trials Visualization](#clinical-trials-visualization)
- [Clinical Trials Map](#clinical-trials-map)
- [Testing](#testing)
- [Performance Features](#performance-features)
- [Documentation](#documentation)
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

## Technology Stack

<p align="center">
<a href="https://www.r-project.org/"><img src="https://img.shields.io/badge/-R-276DC3?logo=r&logoColor=white" alt="R" /></a>
<a href="https://github.com/rstudio/shiny"><img src="https://img.shields.io/badge/-Shiny-276DC3?logoColor=white" alt="Shiny" /></a>
<a href="https://www.python.org/"><img src="https://img.shields.io/badge/-Python-3776AB?logo=python&logoColor=white" alt="Python" /></a>
<a href="https://github.com/twbs/bootstrap"><img src="https://img.shields.io/badge/-Bootstrap%205-7952B3?logo=bootstrap&logoColor=white" alt="Bootstrap 5" /></a>
<a href="https://github.com/Rdatatable/data.table"><img src="https://img.shields.io/badge/-data.table-333333?logoColor=white" alt="data.table" /></a>
<a href="https://github.com/rstudio/DT"><img src="https://img.shields.io/badge/-DT-333333?logoColor=white" alt="DT" /></a>
<a href="https://github.com/rstudio/leaflet"><img src="https://img.shields.io/badge/-Leaflet-199900?logo=leaflet&logoColor=white" alt="Leaflet" /></a>
<a href="https://github.com/atomiks/tippyjs"><img src="https://img.shields.io/badge/-Tippy.js-333333?logoColor=white" alt="Tippy.js" /></a>
<a href="https://www.anthropic.com/claude"><img src="https://img.shields.io/badge/-Claude-191919?logo=anthropic&logoColor=white" alt="Claude" /></a>
<a href="https://github.com/pydantic/pydantic"><img src="https://img.shields.io/badge/-Pydantic-E92063?logo=pydantic&logoColor=white" alt="Pydantic" /></a>
<a href="https://github.com/unionai-oss/pandera"><img src="https://img.shields.io/badge/-Pandera-333333?logoColor=white" alt="Pandera" /></a>
<a href="https://github.com/pandas-dev/pandas"><img src="https://img.shields.io/badge/-pandas-150458?logo=pandas&logoColor=white" alt="pandas" /></a>
<a href="https://github.com/encode/httpx"><img src="https://img.shields.io/badge/-httpx-333333?logoColor=white" alt="httpx" /></a>
<a href="https://github.com/biopython/biopython"><img src="https://img.shields.io/badge/-Biopython-333333?logoColor=white" alt="Biopython" /></a>
<a href="https://github.com/pymupdf/PyMuPDF"><img src="https://img.shields.io/badge/-PyMuPDF-333333?logoColor=white" alt="PyMuPDF" /></a>
<a href="https://github.com/MagicStack/asyncpg"><img src="https://img.shields.io/badge/-asyncpg-333333?logoColor=white" alt="asyncpg" /></a>
<a href="https://www.postgresql.org/"><img src="https://img.shields.io/badge/-PostgreSQL-4169E1?logo=postgresql&logoColor=white" alt="PostgreSQL" /></a>
<a href="https://github.com/sqlalchemy/alembic"><img src="https://img.shields.io/badge/-Alembic-333333?logoColor=white" alt="Alembic" /></a>
<a href="https://www.docker.com/"><img src="https://img.shields.io/badge/-Docker-2496ED?logo=docker&logoColor=white" alt="Docker" /></a>
<a href="https://kubernetes.io/"><img src="https://img.shields.io/badge/-Kubernetes-326CE5?logo=kubernetes&logoColor=white" alt="Kubernetes" /></a>
<a href="https://github.com/grafana/grafana"><img src="https://img.shields.io/badge/-Grafana-F46800?logo=grafana&logoColor=white" alt="Grafana" /></a>
<a href="https://github.com/Textualize/rich"><img src="https://img.shields.io/badge/-Rich-333333?logoColor=white" alt="Rich" /></a>
<a href="https://github.com/r-lib/testthat"><img src="https://img.shields.io/badge/-testthat-333333?logoColor=white" alt="testthat" /></a>
<a href="https://github.com/pytest-dev/pytest"><img src="https://img.shields.io/badge/-pytest-0A9EDC?logo=pytest&logoColor=white" alt="pytest" /></a>
<a href="https://github.com/astral-sh/ruff"><img src="https://img.shields.io/badge/-Ruff-D7FF64?logo=ruff&logoColor=black" alt="Ruff" /></a>
<a href="https://github.com/astral-sh/ty"><img src="https://img.shields.io/badge/-ty-261230?logo=astral&logoColor=white" alt="ty" /></a>
<a href="https://github.com/r-lib/lintr"><img src="https://img.shields.io/badge/-lintr-333333?logoColor=white" alt="lintr" /></a>
</p>

| Layer | Technology |
|-------|-----------|
| Dashboard Framework | [R 4.5+](https://www.r-project.org/), [Shiny](https://github.com/rstudio/shiny), [bslib](https://github.com/rstudio/bslib) (Bootstrap 5 with dark mode) |
| Frontend UI | [DT](https://github.com/rstudio/DT) (DataTables), [shinyWidgets](https://github.com/dreamRs/shinyWidgets), [Tippy.js](https://github.com/atomiks/tippyjs), [Popper.js](https://github.com/floating-ui/floating-ui) |
| Mapping | [Leaflet](https://github.com/rstudio/leaflet), [tidygeocoder](https://github.com/jessecambon/tidygeocoder) (OpenStreetMap Nominatim) |
| Visualization | Custom SVG ([python_plot.py](scripts/python_plot.py)), [Leaflet](https://github.com/rstudio/leaflet) marker clusters |
| Data Processing (R) | [data.table](https://github.com/Rdatatable/data.table), [fastmap](https://github.com/r-lib/fastmap), [memoise](https://github.com/r-lib/memoise), [cachem](https://github.com/r-lib/cachem), [qs](https://github.com/qsbase/qs) |
| Data Processing (Python) | [pandas](https://github.com/pandas-dev/pandas), [Pydantic v2](https://github.com/pydantic/pydantic), [Pandera](https://github.com/unionai-oss/pandera) |
| LLM Extraction | [Anthropic Claude](https://www.anthropic.com/claude) (streaming with adaptive thinking) |
| ETL Pipeline | [Python 3.14+](https://www.python.org/), [httpx](https://github.com/encode/httpx), [Biopython](https://github.com/biopython/biopython), [lxml](https://github.com/lxml/lxml), [PyMuPDF](https://github.com/pymupdf/PyMuPDF), [Rich](https://github.com/Textualize/rich) |
| Bioinformatics (R) | [biomaRt](https://bioconductor.org/packages/biomaRt/), [UniprotR](https://github.com/Proteomicslab57357/UniprotR), [rentrez](https://github.com/ropensci/rentrez), [RefManageR](https://github.com/ropensci/RefManageR) |
| Database | [PostgreSQL 18+](https://www.postgresql.org/), [asyncpg](https://github.com/MagicStack/asyncpg), [RPostgres](https://github.com/r-dbi/RPostgres), [Alembic](https://github.com/sqlalchemy/alembic) |
| Containerization | [Docker](https://www.docker.com/) ([rocker/shiny](https://github.com/rocker-org/rocker-versioned2)) |
| Orchestration & Monitoring | [Kubernetes](https://kubernetes.io/), [NGINX Ingress](https://github.com/kubernetes/ingress-nginx), [Grafana](https://github.com/grafana/grafana), [VictoriaLogs](https://github.com/VictoriaMetrics/VictoriaMetrics) |
| Testing | [testthat](https://github.com/r-lib/testthat), [shinytest2](https://github.com/rstudio/shinytest2), [pytest](https://github.com/pytest-dev/pytest) |
| Linting & Type Checking | [Ruff](https://github.com/astral-sh/ruff), [ty](https://github.com/astral-sh/ty), [lintr](https://github.com/r-lib/lintr) |

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
Interactive SVG sunburst visualization of SVD drugs tested in clinical trials.

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
# Install maRco package (required for data fetching/cleaning)
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

# Batch validation
pandera>=0.18.0

# DataFrame operations
pandas>=2.0.0

# Bioinformatics
biopython>=1.81

# Database
asyncpg>=0.28.0

# Database migrations
alembic>=1.15.0
psycopg2-binary>=2.9.0
sqlalchemy>=2.0.0

# Environment variables
python-dotenv>=1.0.0

# CLI tab-completion
argcomplete>=3.0.0

# CLI output formatting
rich>=13.0.0

# PDF extraction
PyMuPDF>=1.23.0

# Dev tools — linting & type-checking
ruff>=0.9.0
ty>=0.0.1a0
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
   # Initialize core schema
   psql -U csvd_user -d csvd_dashboard -f sql/setup.sql

   # Add external data cache tables
   psql -U csvd_user -d csvd_dashboard -f sql/add_external_data_tables.sql
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
| `ENTREZ_EMAIL` | Email for NCBI Entrez API (required by NCBI policy) | Pipeline only |
| `UNPAYWALL_EMAIL` | Email for Unpaywall open-access PDF API | Pipeline only |
| `PIPELINE_*` | Override any pipeline config parameter (e.g. `PIPELINE_LLM_MODEL`) | Pipeline only |
| `PRELOAD_TABLE2` | Set to FALSE to disable Table 2 preloading (default: TRUE) | Docker/memory optimization |

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

# Run with Table 2 preloading disabled (saves memory)
docker run -p 3838:3838 -e PRELOAD_TABLE2=FALSE svd-dashboard
```

The app will be available at `http://localhost:3838`.

### Kubernetes

See `monitoring/yaml/` for Kubernetes deployment configurations including:
- Monitoring stack deployment
- VictoriaLogs configuration
- Grafana monitoring integration

---

## Project Structure

<details>
<summary><strong>Click to expand project structure</strong></summary>

```
rshiny_dashboard/
├── app.R                         # Main application entry point
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
├── Makevars                      # R compilation flags (OpenMP/clang)
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
│   ├── read_external_data.R      # External data reading from database cache
│   ├── fetch_trial_locations.R   # Trial location fetching and geocoding
│   └── server_map.R              # Clinical Trials Map server logic
├── pipeline/
│   ├── main.py                   # CLI entry point & pipeline orchestrator
│   ├── config.py                 # Centralized configuration with env overrides
│   ├── prompts.py                # LLM prompt templates with prompt caching
│   ├── pubmed_search.py          # PubMed literature search via Entrez
│   ├── pdf_retrieval.py          # Multi-source text retrieval (PMC/Unpaywall/Abstract)
│   ├── llm_extraction.py         # LLM-based gene extraction (Anthropic Claude)
│   ├── validation.py             # NCBI gene verification & confidence filtering
│   ├── batch_validation.py       # Pandera batch quality checks
│   ├── data_merger.py            # Data transformation & database loading
│   ├── database.py               # Async PostgreSQL operations
│   ├── rate_limiter.py           # Token-bucket rate limiter (RPM/TPM)
│   ├── quality_metrics.py        # Pipeline statistics tracking
│   ├── report.py                 # JSON/Rich CLI report generation
│   ├── ncbi_gene_fetch.py        # NCBI Gene data fetching
│   ├── pubmed_citations.py       # PubMed citation handling
│   ├── uniprot_fetch.py          # UniProt data fetching
│   ├── external_data_sync.py     # External data synchronization
│   └── alembic/                  # Database migrations (Alembic)
│       ├── env.py
│       ├── script.py.mako
│       └── versions/
│           └── 001_baseline_schema.py
├── sql/
│   ├── setup.sql                 # Database schema initialization
│   └── add_external_data_tables.sql  # External data table schemas
├── tests/
│   ├── test_all.R                # R test suite (testthat + shinytest2)
│   └── pipeline/                 # Python test suite (pytest)
│       ├── conftest.py           # Shared fixtures
│       ├── test_config.py
│       ├── test_prompts.py
│       ├── test_pubmed_search.py
│       ├── test_pdf_retrieval.py
│       ├── test_llm_extraction.py
│       ├── test_validation.py
│       ├── test_batch_validation.py
│       ├── test_data_merger.py
│       ├── test_database.py
│       ├── test_rate_limiter.py
│       ├── test_quality_metrics.py
│       ├── test_report.py
│       └── test_main.py
├── docs/
│   ├── *.md                      # Primary documentation
│   └── other/                    # Additional docs (ETL overview, etc.)
├── scripts/
│   ├── python_plot.py            # Clinical trials visualization generator
│   └── trigger_update.R          # Regenerate QS files from database
└── www/
    ├── custom.css                # Custom styles (source)
    ├── custom.js                 # Custom JavaScript (source)
    ├── python_plot.html          # Clinical trials visualization
    ├── python_plot.js            # Plot interactivity and sidepanel
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
| `--local-pdfs PATH` | - | Extract genes from local PDF file(s) without PubMed search |
| `--skip-validation` | - | Skip NCBI Gene validation (only with `--local-pdfs`) |

> **Tab-completion**: `eval "$(python pipeline/main.py --complete bash)"` (also supports `zsh` and `fish`)

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
| `geocoded_trials.qs.sha256` | — | SHA256 integrity checksum for geocoded data |

### Automated Updates

> **TODO**: Automated pipeline updates via GitHub Actions are planned but not currently implemented. The previous workflow (`.github/workflows/update_pipeline.yml`) has been removed. For now, run the pipeline manually using the commands above.

**Planned Workflow Steps:**

1. Initialize PostgreSQL 18 service container and load schema
2. Run Python pipeline (`main.py`) to extract new genes
3. Generate QS files via `trigger_update.R`
4. Auto-commit updated `data/qs/*.qs` files to the repository

The QS files are committed directly to the repository, allowing the Shiny app to run without database access in production.

---

## LLM Configuration

### What the LLM Does

Claude reads full-text cSVD research papers and extracts genes with putative causal links, outputting structured JSON with gene symbol, GWAS traits, evidence types (TWAS, PWAS, colocalization, etc.), a calibrated confidence score, and a causal evidence summary. This replaces manual curation — the LLM performs the role of a systematic reviewer, processing papers that would otherwise require domain-expert reading and data entry.

### Prompt Design

The prompt uses a two-part architecture defined in `pipeline/prompts.py`: a system prompt for role assignment and extraction instructions for the task specification.

**System prompt** (`SYSTEM_PROMPT`) — Assigns Claude the role of "a systematic reviewer specializing in cerebral small vessel disease (cSVD) genetics." The [system prompts documentation](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/system-prompts) recommends using the `system` parameter for role assignment, noting it provides "enhanced accuracy" in complex domain scenarios. The highly specific role follows the documentation's advice that more specific roles yield better results.

**Extraction instructions** (`EXTRACTION_INSTRUCTIONS`) — An XML-tagged structure with these components:

| Component | Purpose | Documentation Rationale |
|-----------|---------|------------------------|
| `<task>` | Clear task definition | [Be clear and direct](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/be-clear-and-direct): provide explicit, unambiguous instructions |
| `<inclusion_criteria>` | Enumerates evidence types (GWAS, MR, colocalization, etc.) and canonical cSVD phenotype abbreviations (WMH, SVS, BG-PVS, etc.) | Reduces ambiguity by defining the exact vocabulary the model should use |
| `<extraction_strategy>` | "Identify passages first, then extract" grounding pattern | [Long context tips](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/long-context-tips): "Ground responses in quotes... ask Claude to quote relevant parts first before carrying out its task." Prevents hallucination on 50K-char papers |
| `<field_guidance>` | Maps each output field to extraction rules | [Be clear and direct](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/be-clear-and-direct): use numbered/sequential steps to ensure correct execution |
| `<confidence_scoring>` | 4-tier rubric with anchored examples (1.0 = functional validation, 0.7–0.9 = GWAS + supporting data, etc.) | Calibrates LLM scoring with concrete anchors rather than vague guidance |
| `<examples>` | 3 few-shot examples (2 include, 1 exclude) | [Multishot prompting](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/multishot-prompting): "dramatically improve accuracy, consistency, and quality"; recommends 3–5 diverse examples wrapped in `<example>` tags. The exclude example (APOE as covariate) teaches the critical inclusion boundary |

**Why XML tags throughout**: The [XML tags documentation](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/use-xml-tags) recommends using XML tags to "clearly separate different parts of your prompt" for improved clarity, accuracy, and flexibility. Combining XML tags with multishot prompting (`<examples>`) produces "super-structured, high-performance prompts."

**User message** — Paper text wrapped in `<document source="PubMed" pmid="...">` tags followed by a short extraction query. The [long context tips](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/long-context-tips) recommend wrapping documents in `<document>` tags with metadata, and placing long content at the top with the query at the end — noting this "can improve response quality by up to 30%." User messages are not cached (unique per paper), while system blocks above are cached.

### API Configuration

| Decision | Configuration | Rationale |
|----------|--------------|-----------|
| Model | `claude-opus-4-6` | Most capable model for complex multi-evidence gene extraction requiring domain expertise |
| Streaming | `client.messages.stream()` | Required for adaptive thinking requests that may exceed 10 minutes; raw `AsyncAnthropic` used instead of Instructor because Instructor doesn't support streaming |
| Adaptive thinking | `thinking: {"type": "adaptive"}` | [Adaptive thinking](https://docs.anthropic.com/en/docs/build-with-claude/adaptive-thinking): "reliably drives better performance than extended thinking with a fixed `budget_tokens`"; dynamically allocates reasoning depth per paper |
| Effort level | `"high"` (default) | Balances reasoning depth with token cost. Only sent to API when overridden (since `"high"` is the API default) |
| Structured outputs | `output_config` with JSON schema | [Structured outputs](https://docs.anthropic.com/en/docs/build-with-claude/structured-outputs): constrained decoding "guarantees schema-compliant responses" — always valid JSON, type-safe, no retries needed for schema violations. Schema auto-converted from Pydantic via `transform_schema()` |
| Max output tokens | 32,000 | Accommodates variable adaptive thinking tokens + structured JSON output for papers with many genes |
| Prompt caching | 1h ephemeral TTL on system blocks | [Prompt caching](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching): cache reads cost only 10% of base input price. 1h TTL chosen because paper processing intervals may exceed the 5-min default. System blocks cached; per-paper user messages are not |

### Output Schema

The extraction output is defined by two Pydantic models in `pipeline/llm_extraction.py`:

```python
class GeneEntry(BaseModel):
    gene_symbol: str                              # Official HGNC symbol
    protein_name: str | None = None               # Protein name if mentioned
    gwas_trait: list[str] = []                     # Canonical abbreviations (WMH, SVS, etc.)
    mendelian_randomization: bool = False          # MR evidence in this paper
    omics_evidence: list[str] = []                 # e.g. ["TWAS", "colocalization"]
    confidence: float = Field(ge=0.0, le=1.0)     # Calibrated score per rubric
    causal_evidence_summary: str | None = None     # 1-3 sentence justification
    pmid: str = ""                                 # Set post-extraction by pipeline

class ExtractionResult(BaseModel):
    genes: list[GeneEntry] = []                    # Wrapper for structured output
```

- `confidence` is constrained to [0.0, 1.0] via `Field(ge=0.0, le=1.0)`
- `gwas_trait` and `omics_evidence` are lists (multiple per gene)
- `pmid` is initialized to empty string and assigned post-extraction by the pipeline
- The schema is auto-converted via `transform_schema()` for structured outputs (cached by the API for 24h after first use)

### Post-Extraction Validation

LLM output feeds into a 3-stage validation pipeline (`pipeline/validation.py`):

1. **Confidence threshold** — genes below 0.7 (default) are rejected
2. **NCBI Gene lookup** — verifies the symbol exists in the human genome and normalizes to the official HGNC symbol
3. **GWAS trait check** — warns on unrecognized phenotypes (non-blocking)

### Cost & Rate Limiting

**Pricing** (from `pipeline/report.py`):

| Model | Input (per 1M tokens) | Output (per 1M tokens) |
|-------|----------------------|------------------------|
| Claude Opus 4.6 | $5.00 | $25.00 |

**Prompt caching multipliers**: cache writes at 2x base input price ($10.00/MTok), cache reads at 0.1x ($0.50/MTok). After the first paper, subsequent papers in the same 1-hour window benefit from cached system blocks.

**Cost formula**: `(input_tokens × input_price + cache_write_tokens × input_price × 2.0 + cache_read_tokens × input_price × 0.1 + output_tokens × output_price) / 1,000,000`

**Rate limiting**: A proactive token-bucket rate limiter (`pipeline/rate_limiter.py`) gates requests before they hit the API, preventing 429 errors. On 429, exponential backoff with retry-after header parsing. Up to 5 papers processed concurrently via `asyncio.Semaphore`.

### Configuration Reference

All LLM-related environment variables (from `pipeline/config.py`):

| Variable | Default | Description |
|----------|---------|-------------|
| `PIPELINE_LLM_MODEL` | `claude-opus-4-6` | Model identifier |
| `PIPELINE_LLM_MAX_TOKENS` | `32000` | Max output tokens |
| `PIPELINE_LLM_EFFORT` | `high` | Adaptive thinking effort (`low` / `high` / `max`) |
| `PIPELINE_MAX_PAPER_TEXT_CHARS` | `50000` | Paper text truncation limit (chars) |
| `PIPELINE_CONFIDENCE_THRESHOLD` | `0.7` | Minimum confidence to keep a gene |
| `PIPELINE_MAX_RETRIES` | `1` | Validation error retry budget |
| `PIPELINE_MAX_RATE_LIMIT_RETRIES` | `6` | Rate limit (429) retry budget |
| `PIPELINE_ESTIMATED_TOKENS_PER_CALL` | `40000` | Token estimate for rate limiter (~15K input + thinking + ~4K output) |
| `PIPELINE_RPM_LIMIT` | `50` | Requests per minute |
| `PIPELINE_TPM_LIMIT` | `100000` | Tokens per minute |
| `PIPELINE_MAX_CONCURRENT_PAPERS` | `5` | Parallel paper processing |

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

## Clinical Trials Visualization

The clinical trials visualization is generated by `scripts/python_plot.py` as a pure SVG sunburst chart.
To regenerate the visualization:

```bash
python scripts/python_plot.py
```

This creates `www/python_plot.html` and `www/python_plot.js`.

---

## Clinical Trials Map

An interactive Leaflet map showing global research sites for NCT-registered clinical trials. Only trials registered on ClinicalTrials.gov are mapped — other registries (ISRCTN, ANZCTR, ChiCTR, etc.) are excluded because they lack a comparable location API.

### Data pipeline

1. **NCT ID extraction** — `extract_nct_ids()` filters Table 2 for IDs matching the `NCT` + 8-digit pattern
2. **API fetch** — `fetch_all_trial_locations()` queries the ClinicalTrials.gov API v2 in parallel (4 workers via `future.apply`) with exponential-backoff retries
3. **Geocoding** — `geocode_locations()` resolves city/country strings to coordinates using `tidygeocoder` with the OpenStreetMap Nominatim provider
4. **Caching** — Results are saved to `data/qs/geocoded_trials.qs` with a companion `.sha256` integrity hash; the cache is invalidated automatically when the set of NCT IDs changes

### Map features

- **Marker clustering** with spiderfication on zoom, plus coordinate jittering for co-located facilities
- **HTML popups** with trial metadata: drug name, phase, sponsor, recruitment status, sample size, and estimated completion date
- **Color-coded status badges** (recruiting, active, completed, terminated)
- **Direct links** to each trial's ClinicalTrials.gov page

### Source files

| File | Role |
|------|------|
| `R/fetch_trial_locations.R` | API fetching, geocoding, caching |
| `R/server_map.R` | Leaflet rendering, popups, clustering |

---

## Testing

### R Tests

Run the R test suite (119 testthat + shinytest2 tests):

```bash
Rscript -e 'testthat::test_file("tests/test_all.R")'
```

### Python Tests

Run the pipeline test suite (13 pytest files, 290 tests):

```bash
# Run all pipeline tests
pytest tests/pipeline/

# Verbose with stop-on-first-failure
pytest tests/pipeline/ -x -v
```

Configuration: `asyncio_mode="auto"`, 30s timeout. Markers: `@pytest.mark.slow`, `@pytest.mark.integration`.

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

## Documentation

Detailed documentation is available in the `docs/` directory:

> [!CAUTION]
> The **Dashboard Technical Documentation**, **maRco Package Documentation**, and **Automated Research Pipeline** are currently outdated and do not reflect the latest codebase. Please wait for the updated versions.

| Document | Description |
|----------|-------------|
| ![Outdated](https://img.shields.io/badge/-Outdated-orange) [Dashboard Technical Documentation](docs/dashboard-technical-documentation.md) | Complete technical reference for the Shiny dashboard architecture |
| ![Outdated](https://img.shields.io/badge/-Outdated-orange) [maRco Package Documentation](docs/marco-package-documentation.md) | Developer guide for the maRco R helper package |
| ![Outdated](https://img.shields.io/badge/-Outdated-orange) [Automated Research Pipeline](docs/automated-research-pipeline.md) | Python ETL pipeline for gene extraction from literature |
| [Observability Stack Guide](docs/observability-stack-guide.md) | Kubernetes monitoring and logging setup |

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
