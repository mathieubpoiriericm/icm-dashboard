# Automated Research Pipeline

> **Volume III** of the cSVD Dashboard Documentation
> For the complete documentation, see [`master_documentation.tex`](master_documentation.tex)

---

## Table of Contents

- [Executive Summary](#executive-summary)
- [System Architecture](#system-architecture)
  - [Pipeline Overview](#pipeline-overview)
  - [Directory Structure](#directory-structure)
  - [Database Schema](#database-schema)
  - [External Data Cache Tables](#external-data-cache-tables)
- [Pipeline Components](#pipeline-components)
  - [Database Module](#database-module)
  - [PubMed Search Module](#pubmed-search-module)
  - [PDF Retrieval Module](#pdf-retrieval-module)
  - [LLM Extraction Module](#llm-extraction-module)
- [External Data Sync Module](#external-data-sync-module)
  - [Architecture](#external-data-sync-architecture)
  - [Orchestrator Module](#orchestrator-module)
  - [NCBI Gene Fetch Module](#ncbi-gene-fetch-module)
  - [UniProt Fetch Module](#uniprot-fetch-module)
  - [PubMed Citations Module](#pubmed-citations-module)
- [Data Quality Framework](#data-quality-framework)
  - [Validation Stages](#validation-stages)
  - [Gene Validation Implementation](#gene-validation-implementation)
  - [Trial Validation Implementation](#trial-validation-implementation)
  - [Quality Metrics](#quality-metrics)
- [Dashboard Integration](#dashboard-integration)
  - [Data Merger Module](#data-merger-module)
  - [R Database Connection](#r-database-connection)
  - [R Integration](#r-integration)
  - [R External Data Readers](#r-external-data-readers)
  - [DATA_PATHS Configuration](#data_paths-configuration)
  - [Pipeline Trigger Script](#pipeline-trigger-script)
- [Scheduling and Deployment](#scheduling-and-deployment)
  - [Current GitHub Workflow](#current-github-workflow)
  - [Full Pipeline Workflow](#full-pipeline-workflow)
- [Configuration](#configuration)
  - [Python Requirements](#python-requirements)
  - [Environment Variables](#environment-variables)
  - [CLI Flags](#cli-flags)
  - [Logging System](#logging-system)
  - [Rate Limiting and Concurrency](#rate-limiting-and-concurrency)
  - [Gene Validation Cache](#gene-validation-cache)
- [Cost Estimate](#cost-estimate)

---

## Executive Summary

This document describes an automated pipeline for maintaining the cerebral small vessel disease (cSVD) R Shiny dashboard with current research. The system automates:

1. **Discovery**: Weekly PubMed searches for new cSVD publications
2. **Retrieval**: Full-text PDF acquisition from open-access sources
3. **Extraction**: LLM-powered extraction of genes and clinical trial data
4. **Validation**: Multi-stage quality control with confidence scoring
5. **Integration**: Automated merging with existing dashboard data

> **Note: Implementation Status**
>
> This pipeline is fully implemented and operational. Key components:
> 1. **Python Pipeline**: Automated data extraction and validation (`pipeline/`)
> 2. **R Integration**: Data regeneration script (`scripts/trigger_update.R`)
> 3. **GitHub Actions**: Weekly scheduled updates via workflow configuration

The pipeline uses Python for extraction logic and integrates with the existing R dashboard architecture:

- PostgreSQL database (`csvd_dashboard`) for data storage
- Existing `with_db_connection()` utility in `R/utils.R`
- Existing cleaning scripts (`clean_table1.R`, `clean_table2.R`)
- Helper functions in the `maRco/` R package
- Environment variables: `DB_USER` and `DB_PASSWORD`

---

## System Architecture

### Pipeline Overview

The pipeline operates in two modes: (1) paper processing mode for discovering and extracting gene data from new publications, and (2) external data sync mode for populating cache tables with NCBI, UniProt, and PubMed data.

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   PubMed    │────▶│    New      │────▶│ Deduplication│
│   Search    │     │   PMIDs     │     │   Filter    │
└─────────────┘     └─────────────┘     └──────┬──────┘
                                               │
       ┌───────────────────────────────────────┘
       ▼
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│ PMC/Unpaywall│────▶│  Full-Text  │────▶│    PDF     │
│  Retrieval  │     │    PDFs     │     │  Parsing   │
└─────────────┘     └─────────────┘     └──────┬──────┘
                                               │
       ┌───────────────────────────────────────┘
       ▼
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│ Claude API  │────▶│ Structured  │────▶│  Quality   │
│ Extraction  │     │    JSON     │     │ Validation │
└─────────────┘     └─────────────┘     └──────┬──────┘
                                               │
       ┌───────────────────────────────────────┘
       ▼
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│    Data     │────▶│ PostgreSQL  │────▶│External APIs│
│   Merge     │     │(genes,trials)│     │(NCBI,UniProt)│
└─────────────┘     └─────────────┘     └──────┬──────┘
                                               │
       ┌───────────────────────────────────────┘
       ▼
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│External Data│────▶│Cache Tables │────▶│ R Cleaning │
│    Sync     │     │(ncbi,uniprot)│    │  Scripts   │
└─────────────┘     └─────────────┘     └──────┬──────┘
                                               │
       ┌───────────────────────────────────────┘
       ▼
┌─────────────┐     ┌─────────────┐
│     QS      │────▶│   Shiny    │
│    Files    │     │ Dashboard  │
└─────────────┘     └─────────────┘
```

### Directory Structure

#### Current Directory Structure

The following directories and files exist in the codebase. Items marked with `*` are gitignored and must be created locally or are generated by the pipeline.

```bash
rshiny_dashboard/
├── app.R                    # Main application entry point
├── R/                       # R modules
│   ├── constants.R          # Application-wide constants
│   ├── utils.R              # CSS styles, utilities, and DB connection
│   ├── data_prep.R          # Data loading/preprocessing
│   ├── tooltips.R           # Tooltip generation
│   ├── mod_checkbox_filter.R # Shiny checkbox filter module
│   ├── filter_utils.R       # Filter utility functions
│   ├── server_table1.R      # Table 1 server logic
│   ├── server_table2.R      # Table 2 server logic
│   ├── server.R             # Main server logic
│   ├── ui.R                 # UI definition
│   ├── clean_table1.R       # Table 1 data cleaning
│   ├── clean_table2.R       # Table 2 data cleaning
│   ├── read_external_data.R # Read from DB cache tables
│   ├── fetch_ncbi_gene_data.R   # NCBI gene data (legacy)
│   ├── fetch_omim_data.R        # OMIM data
│   ├── fetch_pubmed_data.R      # PubMed references
│   ├── fetch_uniprot_data.R     # UniProt protein data
│   └── phenogram.R              # Phenogram generation
├── pipeline/                # Python ETL pipeline
├── scripts/                 # Utility scripts
├── misc/
│   ├── md_files/            # Markdown documentation
│   └── yaml_files/          # Kubernetes deployment configs
├── .github/
│   └── workflows/
│       └── dependency-submission.yml  # Dependency graph updates
# --- Gitignored directories (local/generated) ---
├── data/*                   # Generated data files
│   ├── qs/                  # Cached QS files (fast serialization)
│   ├── csv/                 # CSV data files
│   └── xlsx/                # Excel data files
├── sql/*                    # Database schema files
│   ├── setup.sql            # Main schema (genes, clinical_trials, pubmed_refs)
│   ├── add_external_data_tables.sql  # External data cache tables
│   └── common_queries.sql   # Useful query templates
├── logs/*                   # Pipeline log files
├── maRco/*                  # Helper functions R package
├── documentation/*          # LaTeX documentation source
├── tests/*                  # Test files
├── .env*                    # Environment variables (DB credentials)
└── .Renviron*               # R environment variables
```

#### Pipeline Directory Structure

The pipeline implementation consists of the following modules:

```bash
├── pipeline/                # Python pipeline (3,487 lines total)
│   ├── main.py                477  # CLI, logging, orchestration
│   ├── database.py            573  # PostgreSQL async + cache operations
│   ├── pdf_retrieval.py       372  # PDF download with HTTP pooling
│   ├── uniprot_fetch.py       376  # UniProt API fetching (NEW)
│   ├── pubmed_citations.py    373  # PubMed citation formatting (NEW)
│   ├── ncbi_gene_fetch.py     269  # NCBI Gene API fetching (NEW)
│   ├── validation.py          256  # Data validation logic
│   ├── llm_extraction.py      216  # Claude API with rate limit retry
│   ├── data_merger.py         178  # Data merging utilities
│   ├── external_data_sync.py  177  # External data orchestration (NEW)
│   ├── pubmed_search.py       157  # PubMed search with query building
│   ├── quality_metrics.py      63  # Pipeline metrics tracking
│   ├── requirements.txt            # Python dependencies
│   └── README.md                   # Pipeline documentation
├── scripts/
│   └── trigger_update.R       171  # Regenerate QS from database
```

### Database Schema

The pipeline uses PostgreSQL to store gene and clinical trial data. The schema is defined in `sql/setup.sql`:

```sql
-- cSVD Dashboard Database Schema (sql/setup.sql)

-- Table 1: Genes associated with SVD
CREATE TABLE IF NOT EXISTS genes (
    id SERIAL PRIMARY KEY,
    protein VARCHAR(255),
    gene VARCHAR(100) NOT NULL UNIQUE,
    chromosomal_location VARCHAR(50),
    gwas_trait TEXT,
    mendelian_randomization VARCHAR(10),
    evidence_from_other_omics_studies TEXT,
    link_to_monogenetic_disease TEXT,
    brain_cell_types VARCHAR(255),
    affected_pathway TEXT,
    "references" TEXT,  -- Note: quoted to avoid SQL reserved word
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Table 2: Clinical trials for SVD
CREATE TABLE IF NOT EXISTS clinical_trials (
    id SERIAL PRIMARY KEY,
    drug VARCHAR(255) NOT NULL,
    mechanism_of_action TEXT,
    genetic_target VARCHAR(255),
    genetic_evidence VARCHAR(10),
    trial_name TEXT,
    registry_id VARCHAR(50),
    clinical_trial_phase VARCHAR(20),
    svd_population VARCHAR(100),
    svd_population_details TEXT,
    target_sample_size INTEGER,
    estimated_completion_date VARCHAR(20),
    primary_outcome TEXT,
    sponsor_type VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(registry_id, drug)  -- Composite unique constraint
);

-- Track processed PubMed papers to avoid reprocessing
CREATE TABLE IF NOT EXISTS pubmed_refs (
    id SERIAL PRIMARY KEY,
    pmid VARCHAR(20) NOT NULL UNIQUE,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    fulltext_available BOOLEAN DEFAULT FALSE,
    source VARCHAR(50),           -- "pmc", "unpaywall", "abstract"
    genes_extracted INTEGER DEFAULT 0
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_genes_gene ON genes(gene);
CREATE INDEX IF NOT EXISTS idx_trials_registry ON clinical_trials(registry_id);
CREATE INDEX IF NOT EXISTS idx_trials_drug ON clinical_trials(drug);
CREATE INDEX IF NOT EXISTS idx_pubmed_refs_pmid ON pubmed_refs(pmid);

-- Update timestamp trigger function
CREATE OR REPLACE FUNCTION update_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Apply triggers to tables
DROP TRIGGER IF EXISTS genes_updated ON genes;
CREATE TRIGGER genes_updated
    BEFORE UPDATE ON genes
    FOR EACH ROW EXECUTE FUNCTION update_timestamp();

DROP TRIGGER IF EXISTS trials_updated ON clinical_trials;
CREATE TRIGGER trials_updated
    BEFORE UPDATE ON clinical_trials
    FOR EACH ROW EXECUTE FUNCTION update_timestamp();
```

### External Data Cache Tables

The pipeline caches external API data in PostgreSQL to enable fast reads without live API calls. These tables are defined in `sql/add_external_data_tables.sql`:

```sql
-- Migration: Add tables for external data cache
-- These tables store data fetched from NCBI, UniProt, and PubMed APIs
-- to allow trigger_update.R to read from database instead of making API calls

-- NCBI Gene information cache
CREATE TABLE IF NOT EXISTS ncbi_gene_info (
    id SERIAL PRIMARY KEY,
    gene_symbol VARCHAR(100) NOT NULL UNIQUE,
    ncbi_uid VARCHAR(20),
    description TEXT,
    aliases TEXT,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- UniProt protein information cache
CREATE TABLE IF NOT EXISTS uniprot_info (
    id SERIAL PRIMARY KEY,
    gene_symbol VARCHAR(100) NOT NULL UNIQUE,
    accession VARCHAR(20),
    protein_name TEXT,
    biological_process TEXT,
    molecular_function TEXT,
    cellular_component TEXT,
    url TEXT,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- PubMed citation cache
CREATE TABLE IF NOT EXISTS pubmed_citations (
    id SERIAL PRIMARY KEY,
    pmid VARCHAR(20) NOT NULL UNIQUE,
    authors TEXT,
    title TEXT,
    journal TEXT,
    publication_date VARCHAR(50),
    doi VARCHAR(100),
    formatted_ref TEXT,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for efficient lookups
CREATE INDEX IF NOT EXISTS idx_ncbi_gene_symbol ON ncbi_gene_info(gene_symbol);
CREATE INDEX IF NOT EXISTS idx_uniprot_gene_symbol ON uniprot_info(gene_symbol);
CREATE INDEX IF NOT EXISTS idx_pubmed_citations_pmid ON pubmed_citations(pmid);

-- Apply update timestamp triggers
DROP TRIGGER IF EXISTS ncbi_gene_info_updated ON ncbi_gene_info;
CREATE TRIGGER ncbi_gene_info_updated
    BEFORE UPDATE ON ncbi_gene_info
    FOR EACH ROW EXECUTE FUNCTION update_timestamp();

DROP TRIGGER IF EXISTS uniprot_info_updated ON uniprot_info;
CREATE TRIGGER uniprot_info_updated
    BEFORE UPDATE ON uniprot_info
    FOR EACH ROW EXECUTE FUNCTION update_timestamp();

DROP TRIGGER IF EXISTS pubmed_citations_updated ON pubmed_citations;
CREATE TRIGGER pubmed_citations_updated
    BEFORE UPDATE ON pubmed_citations
    FOR EACH ROW EXECUTE FUNCTION update_timestamp();
```

> **Note: Cache Table Summary**
> - `ncbi_gene_info` — NCBI Gene metadata (uid, description, aliases)
> - `uniprot_info` — UniProt protein annotations (GO terms, accession)
> - `pubmed_citations` — Pre-formatted PubMed citation HTML
>
> These tables are populated by `python pipeline/main.py --sync-external-data` and read by `R/read_external_data.R`.

---

## Pipeline Components

### Database Module

The database module manages PostgreSQL connections using async Python:

```python
import asyncpg
from contextlib import asynccontextmanager
from typing import Final
import os

# Configuration from individual environment variables
DB_HOST: Final[str | None] = os.getenv("DB_HOST")
DB_NAME: Final[str | None] = os.getenv("DB_NAME")
DB_USER: Final[str | None] = os.getenv("DB_USER")
DB_PORT: Final[int] = int(os.getenv("DB_PORT", "5432"))
DB_PASSWORD: Final[str | None] = os.getenv("DB_PASSWORD")

# Whitelist for dynamic SQL (prevents SQL injection)
ALLOWED_TABLES: Final[frozenset[str]] = frozenset({"genes", "pubmed_refs"})
ALLOWED_COLUMNS: Final[frozenset[str]] = frozenset({"id"})

class Database:
    """Async connection pool manager using singleton pattern."""
    _pool: asyncpg.Pool | None = None

    @classmethod
    async def get_pool(cls) -> asyncpg.Pool:
        if cls._pool is None:
            cls._pool = await asyncpg.create_pool(
                host=DB_HOST, port=DB_PORT, user=DB_USER,
                password=DB_PASSWORD, database=DB_NAME,
                min_size=2, max_size=10, command_timeout=60.0
            )
        return cls._pool

    @classmethod
    @asynccontextmanager
    async def connection(cls):
        pool = await cls.get_pool()
        async with pool.acquire() as conn:
            yield conn

async def reset_sequence(table: str, column: str = "id") -> None:
    """Reset sequence to avoid PK conflicts (with whitelist validation)."""
    if table not in ALLOWED_TABLES:
        raise ValueError(f"Table '{table}' not in allowed list")
    if column not in ALLOWED_COLUMNS:
        raise ValueError(f"Column '{column}' not in allowed list")

    async with Database.connection() as conn:
        # Defense-in-depth: use quote_ident even after whitelist check
        safe_table = await conn.fetchval("SELECT quote_ident($1)", table)
        safe_column = await conn.fetchval("SELECT quote_ident($1)", column)
        await conn.execute(f"""
            SELECT setval('{table}_{column}_seq',
                COALESCE((SELECT MAX({safe_column}) FROM {safe_table}), 0) + 1, false)
        """)

async def record_processed_pmid(pmid: str, fulltext_available: bool = False,
                                 source: str = "abstract", genes_extracted: int = 0):
    """Record processed PMID to avoid reprocessing (upsert)."""
    async with Database.connection() as conn:
        await conn.execute("""
            INSERT INTO pubmed_refs (pmid, fulltext_available, source, genes_extracted)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (pmid) DO UPDATE SET
                fulltext_available = EXCLUDED.fulltext_available,
                source = EXCLUDED.source,
                genes_extracted = EXCLUDED.genes_extracted,
                processed_at = CURRENT_TIMESTAMP
        """, pmid, fulltext_available, source, genes_extracted)
```

### PubMed Search Module

The search module queries PubMed weekly for new SVD publications:

```python
from Bio import Entrez
from datetime import datetime, timedelta
from typing import Final
import os

# Lazy configuration (ENTREZ_EMAIL required by NCBI policy)
def _configure_entrez() -> None:
    Entrez.email = os.getenv("ENTREZ_EMAIL", "")
    Entrez.api_key = os.getenv("ENTREZ_KEY") or os.getenv("NCBI_API_KEY")

# Search term constants for cSVD/SVD research
DISEASE_TERMS: Final[tuple[str, ...]] = (
    "cerebral small vessel disease", "small vessel disease",
)
MARKER_TERMS: Final[tuple[str, ...]] = (
    "stroke", "dementia", "lacunes", "white matter hyperintensities",
    "perivascular spaces", "cerebral microbleeds",
)
GENETIC_TERMS: Final[tuple[str, ...]] = (
    "gene", "genetic", "GWAS", "EWAS", "TWAS", "PWAS",
    "genome-wide", "variant", "mutation", "polymorphism",
)

def _build_query() -> str:
    """Build PubMed query from term constants."""
    disease_clause = " OR ".join(f'"{t}"[Title/Abstract]' for t in DISEASE_TERMS)
    genetic_clause = " OR ".join(f'"{t}"[Title/Abstract]' for t in GENETIC_TERMS)
    marker_clause = " OR ".join(f'"{t}"[Title/Abstract]' for t in MARKER_TERMS)
    main = f"(({disease_clause}) AND ({genetic_clause}))"
    marker = f"(({marker_clause}) AND ({disease_clause}))"
    return f"{main} OR {marker}"

SVD_QUERY: Final[str] = _build_query()

def search_recent_papers(days_back: int = 7) -> list[str]:
    """Return PMIDs of papers published in the last N days."""
    _configure_entrez()
    mindate = (datetime.now() - timedelta(days=days_back)).strftime("%Y/%m/%d")
    handle = Entrez.esearch(db="pubmed", term=SVD_QUERY, mindate=mindate,
                            maxdate="3000", retmax=500, usehistory="y")
    return Entrez.read(handle).get("IdList", [])
```

### PDF Retrieval Module

Attempts full-text retrieval from multiple sources:

```python
import httpx
from typing import Optional

UNPAYWALL_EMAIL = "your.email@institution.edu"

async def get_fulltext(pmid: str, doi: Optional[str]) -> dict:
    """Attempt full-text retrieval, return text + source."""

    # Try PubMed Central first
    pmc_text = await fetch_pmc_fulltext(pmid)
    if pmc_text:
        return {"text": pmc_text, "source": "pmc", "fulltext": True}

    # Try Unpaywall for OA PDF
    if doi:
        oa_url = await check_unpaywall(doi)
        if oa_url:
            pdf_text = await download_and_parse_pdf(oa_url)
            if pdf_text:
                return {"text": pdf_text, "source": "unpaywall", "fulltext": True}

    # Fallback to abstract only
    abstract = await fetch_abstract(pmid)
    return {"text": abstract, "source": "abstract", "fulltext": False}

async def check_unpaywall(doi: str) -> Optional[str]:
    """Query Unpaywall API for open-access PDF URL."""
    url = f"https://api.unpaywall.org/v2/{doi}?email={UNPAYWALL_EMAIL}"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("is_oa") and data.get("best_oa_location"):
                return data["best_oa_location"].get("url_for_pdf")
    return None
```

### LLM Extraction Module

Uses Claude API with structured prompts, rate limit handling, and multi-tier JSON parsing:

```python
import anthropic
import asyncio
import json
import random
import re
from typing import Final

# Rate limiting and retry configuration
MAX_RETRIES: Final[int] = 5
BASE_RETRY_DELAY: Final[float] = 60.0  # Start with 60s on rate limit
MAX_RETRY_DELAY: Final[float] = 300.0  # Cap at 5 minutes
JITTER_FACTOR: Final[float] = 0.25     # Add up to 25% random jitter

# Async client singleton for connection reuse
_async_client: anthropic.AsyncAnthropic | None = None

async def extract_from_paper(text: str, pmid: str) -> list[dict]:
    """Extract genes with exponential backoff retry for rate limits."""
    client = _get_async_client()
    last_error = None

    for attempt in range(MAX_RETRIES):
        try:
            response = await client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=4096,
                messages=[{"role": "user",
                           "content": f"{EXTRACTION_PROMPT}\n\n---\nPMID: {pmid}\n\n{text[:50000]}"}]
            )
            return parse_llm_response(response.content[0].text)
        except anthropic.RateLimitError as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                # Exponential backoff with jitter
                delay = min(BASE_RETRY_DELAY * (2 ** attempt), MAX_RETRY_DELAY)
                jitter = delay * JITTER_FACTOR * random.random()
                await asyncio.sleep(delay + jitter)
    if last_error:
        raise last_error
    return []

def parse_llm_response(text: str) -> list[dict]:
    """4-tier fallback for parsing LLM JSON output."""
    # Tier 1: Extract from markdown code blocks
    json_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if json_match:
        text = json_match.group(1)
    # Tier 2: Direct JSON parsing
    try:
        parsed = json.loads(text.strip())
        if isinstance(parsed, dict):
            return parsed.get("genes", [])
    except json.JSONDecodeError:
        pass
    # Tier 3: Find JSON object anywhere in text
    json_obj_match = re.search(r"\{[\s\S]*\}", text)
    if json_obj_match:
        try:
            return json.loads(json_obj_match.group()).get("genes", [])
        except json.JSONDecodeError:
            pass
    # Tier 4: Extract genes array directly
    genes_match = re.search(r'"genes"\s*:\s*(\[[\s\S]*?\])', text)
    if genes_match:
        try:
            return json.loads(genes_match.group(1))
        except json.JSONDecodeError:
            pass
    return []
```

---

## External Data Sync Module

The external data sync module fetches and caches data from NCBI Gene, UniProt, and PubMed APIs. This enables the R dashboard to read from PostgreSQL instead of making live API calls during data regeneration.

### External Data Sync Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  NCBI Gene  │     │   UniProt   │     │   PubMed    │
│     API     │     │  REST API   │     │ E-utilities │
└──────┬──────┘     └──────┬──────┘     └──────┬──────┘
       │                   │                   │
       ▼                   ▼                   ▼
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│ ncbi_gene   │     │  uniprot    │     │  pubmed     │
│ _fetch.py   │     │ _fetch.py   │     │_citations.py│
└──────┬──────┘     └──────┬──────┘     └──────┬──────┘
       │                   │                   │
       └───────────────────┼───────────────────┘
                           ▼
                   ┌─────────────┐
                   │external_data│
                   │ _sync.py    │
                   │(Orchestrator)│
                   └──────┬──────┘
                          │
                          ▼
                   ┌─────────────┐
                   │ PostgreSQL  │
                   │   Cache     │
                   │ (3 tables)  │
                   └──────┬──────┘
                          │
                          ▼
                   ┌─────────────┐
                   │R/read_      │
                   │external_    │
                   │data.R       │
                   └─────────────┘
```

### Orchestrator Module

The orchestrator coordinates all external data fetching:

```python
@dataclass(slots=True)
class ExternalDataSyncResult:
    """Combined result from all external data sync operations."""
    ncbi_fetched: int = 0
    ncbi_cached: int = 0
    ncbi_failed: int = 0
    uniprot_fetched: int = 0
    uniprot_cached: int = 0
    uniprot_failed: int = 0
    pubmed_fetched: int = 0
    pubmed_cached: int = 0
    pubmed_failed: int = 0
    errors: list[str] = field(default_factory=list)

async def sync_all_external_data() -> ExternalDataSyncResult:
    """Sync all external data sources for dashboard refresh.

    This function:
    1. Gets all gene symbols from genes and clinical_trials tables
    2. Extracts PMIDs from genes.references column
    3. Syncs NCBI gene info for all genes
    4. Syncs UniProt info for Table 1 genes
    5. Syncs PubMed citations for all PMIDs
    """
    result = ExternalDataSyncResult()

    # Step 1: Get gene symbols
    table1_genes = await get_table1_gene_symbols()
    table2_genes = await get_table2_gene_symbols()
    all_genes = list(dict.fromkeys(table1_genes + table2_genes))

    # Step 2: Get PMIDs from references
    pmids = await get_all_pmids()

    # Step 3-5: Sync each data source
    ncbi_result = await sync_ncbi_gene_info(all_genes)
    uniprot_result = await sync_uniprot_info(table1_genes)
    pubmed_result = await sync_pubmed_citations(pmids)

    # Aggregate results...
    return result
```

### NCBI Gene Fetch Module

Fetches gene metadata from NCBI Gene database:

```python
@dataclass(slots=True)
class NCBIGeneInfo:
    """NCBI Gene information for a single gene."""
    gene_symbol: str
    ncbi_uid: str | None
    description: str | None
    aliases: str | None

# Rate limiting for NCBI (3 req/sec without API key, 10 with)
NCBI_RATE_LIMIT: Final[int] = 10
_ncbi_semaphore = asyncio.Semaphore(NCBI_RATE_LIMIT)

async def fetch_ncbi_gene_info(gene_symbol: str) -> NCBIGeneInfo | None:
    """Fetch NCBI gene information with caching and rate limiting."""
    async with _ncbi_semaphore:
        # Step 1: Search for gene ID
        search_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        params = {
            "db": "gene",
            "term": f"{gene_symbol}[Gene Name] AND Homo sapiens[Organism]",
            "retmode": "json",
        }
        # ... fetch and parse response
        return NCBIGeneInfo(
            gene_symbol=gene_symbol,
            ncbi_uid=gene_id,
            description=gene_data.get("description", ""),
            aliases=gene_data.get("otheraliases", ""),
        )

async def sync_ncbi_gene_info(gene_symbols: list[str]) -> SyncResult:
    """Sync NCBI gene info to database, checking cache first."""
    cached_genes = await get_cached_ncbi_genes(gene_symbols)
    symbols_to_fetch = [s for s in gene_symbols if s not in cached_genes]
    # Fetch missing, store in database
    return SyncResult(fetched=len(successful), cached=len(cached_genes), ...)
```

### UniProt Fetch Module

Fetches protein annotations including GO terms:

```python
@dataclass(slots=True)
class UniProtInfo:
    """UniProt protein information for a single gene."""
    gene_symbol: str
    accession: str | None
    protein_name: str | None
    biological_process: str | None
    molecular_function: str | None
    cellular_component: str | None
    url: str | None

UNIPROT_RATE_LIMIT: Final[int] = 5
UNIPROT_BASE_URL: Final[str] = "https://rest.uniprot.org/uniprotkb/search"

async def fetch_uniprot_info(gene_symbol: str) -> UniProtInfo | None:
    """Fetch complete UniProt information for a gene symbol."""
    # Step 1: Get accession via TSV search
    accession, protein_name = await fetch_uniprot_accession(gene_symbol)
    if not accession:
        return UniProtInfo(gene_symbol=gene_symbol, ...)  # Empty info

    # Step 2: Fetch GO annotations
    go_info = await fetch_uniprot_go_info(accession)

    return UniProtInfo(
        gene_symbol=gene_symbol,
        accession=accession,
        protein_name=protein_name,
        biological_process=go_info["biological_process"],
        molecular_function=go_info["molecular_function"],
        cellular_component=go_info["cellular_component"],
        url=f"https://www.uniprot.org/uniprotkb/{accession}/entry",
    )
```

### PubMed Citations Module

Fetches and formats PubMed citations as HTML:

```python
@dataclass(slots=True)
class PubMedCitation:
    """PubMed citation information."""
    pmid: str
    authors: str | None
    title: str | None
    journal: str | None
    publication_date: str | None
    doi: str | None
    formatted_ref: str  # Pre-formatted HTML for dashboard display

def _format_citation(authors, title, journal, pub_date, doi) -> str:
    """Format citation as HTML string for display."""
    parts = []
    if authors:
        parts.append(f"<b>{authors}</b>")
    if title:
        parts.append(f"<i>{_title_case(title)}</i>")
    if journal:
        journal_part = journal
        if pub_date:
            journal_part += f" ({pub_date})"
        parts.append(journal_part)
    if doi:
        parts.append(f"DOI: {doi}")
    return "<br>".join(parts)

def extract_pmids_from_text(text: str) -> list[str]:
    """Extract unique PMIDs from text containing references."""
    # Find all 7-8 digit numbers (typical PMID format)
    pmids = re.findall(r"\b(\d{7,8})\b", text)
    return list(dict.fromkeys(pmids))  # Preserve order, remove duplicates
```

---

## Data Quality Framework

Quality control is the most critical component. The pipeline implements a multi-stage validation system.

### Validation Stages

| Stage | Type | Description |
|-------|------|-------------|
| 1 | Confidence Filter | Reject entries with confidence < 0.7 |
| 2 | Gene Validation | Verify gene symbols via NCBI Gene API |
| 3 | GWAS Trait Validation | Validate against known SVD GWAS traits |
| 4 | OMIM Validation | Verify OMIM numbers if provided |
| 5 | Trial Validation | Verify registry IDs via multiple registries |
| 6 | Schema Validation | Enforce required fields and formats |

### Gene Validation Implementation

```python
from dataclasses import dataclass
from typing import List, Optional
import httpx

@dataclass
class ValidationResult:
    is_valid: bool
    errors: List[str]
    warnings: List[str]
    normalized_data: Optional[dict]

CONFIDENCE_THRESHOLD = 0.7

async def validate_gene_entry(entry: dict) -> ValidationResult:
    """Multi-stage gene validation."""
    errors, warnings = [], []

    # Stage 1: Confidence threshold
    if entry.get("confidence", 0) < CONFIDENCE_THRESHOLD:
        errors.append(f"Low confidence: {entry['confidence']}")
        return ValidationResult(False, errors, warnings, None)

    # Stage 2: NCBI Gene validation
    gene_symbol = entry["gene_symbol"]
    ncbi_info = await verify_ncbi_gene(gene_symbol)

    if not ncbi_info:
        errors.append(f"Gene '{gene_symbol}' not found in NCBI Gene")
        return ValidationResult(False, errors, warnings, None)

    # Normalize gene symbol to official NCBI symbol
    if ncbi_info["symbol"] != gene_symbol:
        warnings.append(f"Normalized '{gene_symbol}' -> '{ncbi_info['symbol']}'")
        entry["gene_symbol"] = ncbi_info["symbol"]

    # Stage 3: GWAS trait validation (matches dashboard's actual GWAS traits)
    valid_traits = {"WMH", "SVS", "BG-PVS", "WM-PVS", "HIP-PVS",
                    "PSMD", "extreme-cSVD", "FA", "lacunes", "stroke"}
    for trait in entry.get("gwas_trait", []):
        if trait not in valid_traits:
            warnings.append(f"Unknown GWAS trait: {trait}")

    # Stage 4: OMIM validation (if provided)
    if entry.get("omim_number"):
        omim_valid = await verify_omim_number(entry["omim_number"])
        if not omim_valid:
            warnings.append(f"OMIM {entry['omim_number']} not verified")

    return ValidationResult(True, errors, warnings, entry)

async def verify_ncbi_gene(symbol: str) -> Optional[dict]:
    """Query NCBI Gene database to verify gene symbol."""
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    params = {
        "db": "gene",
        "term": f"{symbol}[Gene Name] AND Homo sapiens[Organism]",
        "retmode": "json"
    }
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params=params)
        data = resp.json()
        if data["esearchresult"]["count"] != "0":
            gene_id = data["esearchresult"]["idlist"][0]
            return await fetch_gene_details(gene_id)
    return None
```

### Trial Validation Implementation

```python
async def validate_trial_entry(entry: dict) -> ValidationResult:
    """Validate clinical trial entry against registries."""
    errors, warnings = [], []

    # Stage 1: Confidence threshold
    if entry.get("confidence", 0) < CONFIDENCE_THRESHOLD:
        errors.append(f"Low confidence: {entry['confidence']}")
        return ValidationResult(False, errors, warnings, None)

    # Stage 2: Registry ID validation (supports NCT, ISRCTN, ACTRN, ChiCTR)
    registry_id = entry.get("registry_ID", "")
    if registry_id:
        if registry_id.startswith("NCT"):
            valid = await verify_clinicaltrials_gov(registry_id)
        elif registry_id.startswith("ISRCTN"):
            valid = await verify_isrctn(registry_id)
        elif registry_id.startswith("ChiCTR"):
            valid = await verify_chictr(registry_id)
        elif registry_id.startswith("ACTRN"):
            valid = await verify_anzctr(registry_id)
        else:
            valid = True  # Accept other registries with warning
            warnings.append(f"Registry {registry_id} not auto-verified")

        if not valid:
            errors.append(f"Registry ID {registry_id} not found")
            return ValidationResult(False, errors, warnings, None)
    else:
        warnings.append("No registry ID provided")

    # Stage 3: Required fields (matches table2.csv schema)
    required = ["drug", "mechanism_of_action", "svd_population"]
    for field in required:
        if not entry.get(field):
            errors.append(f"Missing required field: {field}")

    if errors:
        return ValidationResult(False, errors, warnings, None)

    return ValidationResult(True, errors, warnings, entry)
```

### Quality Metrics

```python
@dataclass
class PipelineMetrics:
    papers_processed: int = 0
    fulltext_retrieved: int = 0
    abstract_only: int = 0
    genes_extracted: int = 0
    genes_validated: int = 0
    genes_rejected: int = 0
    trials_extracted: int = 0
    trials_validated: int = 0
    trials_rejected: int = 0

    @property
    def gene_acceptance_rate(self) -> float:
        if self.genes_extracted == 0:
            return 0.0
        return self.genes_validated / self.genes_extracted
```

---

## Dashboard Integration

### Data Merger Module

The data merger module handles merging validated entries into the PostgreSQL database:

```python
from typing import List, Optional
from pipeline.database import (
    Database, get_existing_genes, get_existing_trials,
    insert_gene, insert_trial
)

async def merge_gene_entries(new_entries: List[dict]) -> dict:
    """Merge new gene entries into the PostgreSQL database.

    Database schema (matches R/clean_table1.R expectations):
    - protein, gene, chromosomal_location, gwas_trait,
    - mendelian_randomization, evidence_from_other_omics_studies,
    - link_to_monogenetic_disease, brain_cell_types,
    - affected_pathway, references

    Returns dict with counts of inserted/updated entries.
    """
    existing_genes = await get_existing_genes()
    inserted, updated = 0, 0

    for entry in new_entries:
        gene = entry["gene_symbol"].upper()
        gene_data = {
            "protein": entry.get("protein_name", entry["gene_symbol"]),
            "gene": entry["gene_symbol"],
            "chromosomal_location": entry.get("chromosomal_location", ""),
            "gwas_trait": ", ".join(entry.get("gwas_trait", [])),
            "mendelian_randomization": "Y" if entry.get("mendelian_randomization") else "",
            "evidence_from_other_omics_studies": format_omics(entry.get("omics_evidence", [])),
            "link_to_monogenetic_disease": format_omim(entry.get("omim_number")),
            "brain_cell_types": entry.get("brain_cell_types", ""),
            "affected_pathway": entry.get("affected_pathway", ""),
            "references": entry.get("pmid", "")
        }

        await insert_gene(gene_data)
        if gene in existing_genes:
            updated += 1
        else:
            inserted += 1

    return {"inserted": inserted, "updated": updated}

def format_omics(evidence: List[str]) -> str:
    """Format omics evidence for database storage.

    Raw format uses '*' suffix and ';' separators.
    R/clean_table1.R strips the '*' and converts ';' to ', '.
    Supported omics types: TWAS, PWAS, EWAS
    """
    # e.g., ["TWAS:brain", "PWAS:blood"] -> "TWAS:brain*;PWAS:blood*;"
    return ";".join(f"{e}*" for e in evidence) + ";" if evidence else ""

def format_omim(omim_number: Optional[str]) -> str:
    """Format OMIM number for link_to_monogenetic_disease column."""
    if not omim_number:
        return ""
    return omim_number
```

### R Database Connection

The database connection code currently exists in `R/utils.R`:

```r
#' Execute Function with Database Connection
#'
#' Manages database connection lifecycle. If a connection is provided, uses it
#' directly. Otherwise, creates a new connection, executes the function, and
#' ensures proper cleanup.
#'
#' @param fn Function to execute. Receives the connection as its first argument.
#' @param con Optional existing DBI connection. If NULL, a new connection
#'   is created.
#' @param dbname Database name. Defaults to "csvd_dashboard".
#' @param host Database host. Defaults to "localhost".
#' @param port Database port. Defaults to 5432.
#' @param user Database user. Defaults to Sys.getenv("PGUSER").
#' @param password Database password. Defaults to Sys.getenv("PGPASSWORD").
#'
#' @return The result of executing fn with the connection.
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
      dbname = dbname,
      host = host,
      port = port,
      user = user,
      password = password
    )
    close_con <- TRUE
  }

  on.exit({
    if (close_con) {
      DBI::dbDisconnect(con)
    }
  })

  fn(con)
}

# Example usage:
# result <- with_db_connection(function(con) {
#   DBI::dbGetQuery(con, "SELECT * FROM genes")
# })
```

### R Integration

The R cleaning functions fetch and process data in a single call, using `with_db_connection()` internally for connection management:

**R/clean_table1.R — Unified Fetch and Clean Function:**

```r
#' Clean Table 1 Data
#'
#' Reads and cleans the genes table from PostgreSQL in one call.
#' Uses with_db_connection() internally for automatic connection lifecycle.
#'
#' @param con Optional existing DBI connection. If NULL, creates new connection.
#' @param dbname Database name. Defaults to "csvd_dashboard".
#' @param host Database host. Defaults to "localhost".
#' @param port Database port. Defaults to 5432.
#' @param user Database user. Defaults to Sys.getenv("DB_USER").
#' @param password Database password. Defaults to Sys.getenv("DB_PASSWORD").
#' @return A cleaned data.frame ready for display.
clean_table1 <- function(con = NULL, dbname = "csvd_dashboard",
                         host = "localhost", port = 5432,
                         user = Sys.getenv("DB_USER"),
                         password = Sys.getenv("DB_PASSWORD")) {
  # Fetch data using with_db_connection utility
  table1 <- with_db_connection(
    function(conn) {
      df <- DBI::dbGetQuery(conn, "SELECT * FROM genes")
      df$id <- NULL; df$created_at <- NULL; df$updated_at <- NULL
      df
    },
    con = con, dbname = dbname, host = host,
    port = port, user = user, password = password
  )

  # Clean and transform data (GWAS traits, omics evidence, etc.)
  table1[!nzchar(table1, keepNA = TRUE)] <- NA
  names(table1) <- clean_column_names(names(table1))
  table1$`GWAS Trait` <- strsplit(table1$`GWAS Trait`, ", ", fixed = TRUE)
  # ... additional transformations
  table1
}
```

**R/clean_table2.R — Clinical Trials Fetch and Clean:**

```r
#' Clean Table 2 Data
#'
#' Reads and cleans the clinical_trials table from PostgreSQL.
#'
#' @param con Optional existing DBI connection.
#' @return A cleaned data.frame ready for display.
clean_table2 <- function(con = NULL, dbname = "csvd_dashboard",
                         host = "localhost", port = 5432,
                         user = Sys.getenv("DB_USER"),
                         password = Sys.getenv("DB_PASSWORD")) {
  table2 <- with_db_connection(
    function(conn) {
      df <- DBI::dbGetQuery(conn, "SELECT * FROM clinical_trials")
      df$id <- NULL; df$created_at <- NULL; df$updated_at <- NULL
      df
    },
    con = con, dbname = dbname, host = host,
    port = port, user = user, password = password
  )

  names(table2) <- clean_column_names(names(table2))
  table2$`Genetic Target`[is.na(table2$`Genetic Target`)] <- "(none)"
  table2$`Target Sample Size` <- as.character(table2$`Target Sample Size`)
  table2
}
```

### R External Data Readers

The `R/read_external_data.R` module provides functions to read cached external data from PostgreSQL instead of making live API calls. These functions replace the legacy API-calling functions.

> **Note: Migration**
>
> The following legacy R files made live API calls during data regeneration:
> - `fetch_ncbi_gene_data.R` → replaced by `read_ncbi_gene_info_from_db()`
> - `fetch_uniprot_data.R` → replaced by `read_uniprot_data_from_db()`
> - `fetch_pubmed_data.R` → replaced by `read_pubmed_refs_from_db()`
>
> The Python pipeline now handles all API calls via `--sync-external-data`, storing results in PostgreSQL cache tables for R to read.

```r
# read_external_data.R
# Functions to read external data (NCBI, UniProt, PubMed) from database cache
# These replace the API-calling functions in fetch_*.R files

# Read NCBI Gene Info from Database
#
# Retrieves cached NCBI gene information for the specified gene symbols.
# Data is populated by the Python pipeline (main.py --sync-external-data).
#
# Args:
#   gene_symbols: Character vector of gene symbols to look up.
#   con: Optional existing DBI connection.
#
# Returns:
#   Data frame with columns: name, uid, description, otheraliases
read_ncbi_gene_info_from_db <- function(gene_symbols, con = NULL, ...) {
  with_db_connection(function(conn) {
    placeholders <- paste0("$", seq_along(gene_symbols), collapse = ", ")
    query <- sprintf(
      "SELECT gene_symbol as name, ncbi_uid as uid,
              description, aliases as otheraliases
       FROM ncbi_gene_info WHERE gene_symbol IN (%s)", placeholders)
    DBI::dbGetQuery(conn, query, params = as.list(gene_symbols))
  }, con = con, ...)
}

# Read UniProt Data from Database
read_uniprot_data_from_db <- function(gene_symbols, con = NULL, ...) {
  with_db_connection(function(conn) {
    placeholders <- paste0("$", seq_along(gene_symbols), collapse = ", ")
    query <- sprintf(
      "SELECT gene_symbol as gene, accession, protein_name,
              biological_process, molecular_function, cellular_component, url
       FROM uniprot_info WHERE gene_symbol IN (%s)", placeholders)
    DBI::dbGetQuery(conn, query, params = as.list(gene_symbols))
  }, con = con, ...)
}

# Read PubMed References from Database
read_pubmed_refs_from_db <- function(pmids, con = NULL, ...) {
  with_db_connection(function(conn) {
    placeholders <- paste0("$", seq_along(pmids), collapse = ", ")
    query <- sprintf(
      "SELECT pmid, formatted_ref FROM pubmed_citations WHERE pmid IN (%s)",
      placeholders)
    DBI::dbGetQuery(conn, query, params = as.list(pmids))
  }, con = con, ...)
}
```

### DATA_PATHS Configuration

The `R/constants.R` file defines paths to all data files used by the dashboard:

```r
# =============================================================================
# DATA FILE PATHS
# =============================================================================
# Paths to data files (qs format by default, falls back to rds)
DATA_PATHS <- list(
  table1_clean = "data/qs/table1_clean.qs",
  table2_clean = "data/qs/table2_clean.qs",
  gene_info = "data/qs/gene_info_results_df.qs",
  gene_info_table2 = "data/qs/gene_info_table2.qs",
  prot_info = "data/qs/prot_info_clean.qs",
  refs = "data/qs/refs.qs",
  gwas_trait_names = "data/qs/gwas_trait_names.qs",
  omim_info = "data/csv/omim_info.csv"
)
```

### Pipeline Trigger Script

This is an R-only script that regenerates QS files after the Python pipeline updates the database. It reads external data from PostgreSQL cache tables instead of making live API calls.

> **Warning: Prerequisite**
>
> Before running `trigger_update.R`, you must run `python pipeline/main.py --sync-external-data` to populate the external data cache tables. Without this step, the external data reads will return empty results.

```r
# trigger_update.R
# Reads from database and generates QS files for the Shiny dashboard
#
# Prerequisites:
# 1. Run: python pipeline/main.py (to populate genes table)
# 2. Run: python pipeline/main.py --sync-external-data (to populate cache tables)
# 3. Run: Rscript scripts/trigger_update.R (this script)

# Source required R functions
source("R/utils.R")
source("R/clean_table1.R")
source("R/clean_table2.R")
source("R/read_external_data.R")  # NEW: Database cache readers

# Ensure data directories exist
if (!dir.exists("data/qs")) dir.create("data/qs", recursive = TRUE)

# STEP 1: Clean and save Table 1 (genes) from database
message("[1/7] Fetching and cleaning Table 1 (genes) from database...")
table1_clean <- clean_table1()
qs::qsave(table1_clean, "data/qs/table1_clean.qs")
gene_symbols_table1 <- unique(table1_clean$Gene)

# STEP 2: Clean and save Table 2 (clinical trials) from database
message("[2/7] Fetching and cleaning Table 2 (clinical trials)...")
table2_clean <- clean_table2()
qs::qsave(table2_clean, "data/qs/table2_clean.qs")

# STEP 3: Read NCBI gene info from database cache (no API calls)
message("[3/7] Reading NCBI gene info for Table 1 genes from database...")
gene_info_results_df <- read_ncbi_gene_info_from_db(gene_symbols_table1)
qs::qsave(gene_info_results_df, "data/qs/gene_info_results_df.qs")

# STEP 4: Read NCBI gene info for Table 2 genes from database cache
message("[4/7] Reading NCBI gene info for Table 2 genes from database...")
gene_info_table2 <- read_table2_ncbi_info_db()
qs::qsave(gene_info_table2, "data/qs/gene_info_table2.qs")

# STEP 5: Read UniProt protein info from database cache (no API calls)
message("[5/7] Reading UniProt protein info from database...")
prot_info_clean <- read_uniprot_data_from_db(gene_symbols_table1)
qs::qsave(prot_info_clean, "data/qs/prot_info_clean.qs")

# STEP 6: Read PubMed references from database cache (no API calls)
message("[6/7] Reading PubMed references from database...")
pmids <- extract_unique_pmids(table1_clean$References)
if (length(pmids) > 0) {
  refs <- read_pubmed_refs_from_db(pmids)
  qs::qsave(refs, "data/qs/refs.qs")
}

# STEP 7: Extract GWAS trait names
message("[7/7] Extracting GWAS trait names...")
all_gwas_traits <- unique(unlist(table1_clean$`GWAS Trait`))
gwas_trait_names <- data.frame(abbrev = all_gwas_traits, full_name = all_gwas_traits)
qs::qsave(gwas_trait_names, "data/qs/gwas_trait_names.qs")

message("Dashboard data update completed!")
message("Note: External data read from DB cache. Run --sync-external-data to refresh.")
```

> **Note: Generated QS Files**
>
> The trigger script produces 7 QS files in `data/qs/`:
> 1. `table1_clean.qs` — Cleaned gene data (from `genes` table)
> 2. `table2_clean.qs` — Cleaned clinical trials data (from `clinical_trials` table)
> 3. `gene_info_results_df.qs` — NCBI gene information (from `ncbi_gene_info` cache)
> 4. `gene_info_table2.qs` — Gene info for Table 2 (from `ncbi_gene_info` cache)
> 5. `prot_info_clean.qs` — UniProt protein information (from `uniprot_info` cache)
> 6. `refs.qs` — PubMed reference metadata (from `pubmed_citations` cache)
> 7. `gwas_trait_names.qs` — GWAS trait abbreviation mapping
>
> Steps 3-6 now read from PostgreSQL cache tables instead of making live API calls. The cache is populated by `python pipeline/main.py --sync-external-data`.

---

## Scheduling and Deployment

### Current GitHub Workflow

The following workflow currently exists and updates the dependency graph:

```yaml
name: Dependency Submission

on:
  push:
    branches: [main]
    paths:
      - "app.R"
      - "maRco/DESCRIPTION"
      - "dependency-graph.json"
  workflow_dispatch:

permissions:
  contents: read

jobs:
  dependency-submission:
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Update dependency graph metadata
        run: |
          SHORT_SHA=$(git rev-parse --short HEAD)
          sed -i "s/\"sha\": \"[^\"]*\"/\"sha\": \"$SHORT_SHA\"/" dependency-graph.json
          CURRENT_DATE=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
          sed -i "s/\"scanned\": \"[^\"]*\"/\"scanned\": \"$CURRENT_DATE\"/" dependency-graph.json

      - name: Submit dependency graph
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          curl -X POST \
            -H "Accept: application/vnd.github+json" \
            -H "Authorization: Bearer $GH_TOKEN" \
            -H "X-GitHub-Api-Version: 2022-11-28" \
            https://api.github.com/repos/${{ github.repository }}/dependency-graph/snapshots \
            -d @dependency-graph.json
```

### Full Pipeline Workflow

The following workflow can run weekly to update the pipeline (currently schedule is disabled for manual testing):

```yaml
name: Weekly cSVD Pipeline Update

on:
  # schedule:
  #   - cron: "0 6 * * 1"  # Every Monday at 6 AM UTC (disabled)
  workflow_dispatch:       # Manual trigger only

jobs:
  update:
    runs-on: macos-latest

    services:
      postgres:
        image: postgres:18  # Updated to PostgreSQL 18
        env:
          POSTGRES_USER: csvd_user
          POSTGRES_PASSWORD: ${{ secrets.DB_PASSWORD }}
          POSTGRES_DB: csvd_dashboard
        ports:
          - 5432:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5

    steps:
      - uses: actions/checkout@v4

      - name: Initialize database schema
        env:
          PGPASSWORD: ${{ secrets.DB_PASSWORD }}
        run: |
          psql -h localhost -U csvd_user -d csvd_dashboard \
            -f sql/setup.sql  # Note: sql/ not misc/sql_files/

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.14"  # Updated to Python 3.14

      - name: Install Python dependencies
        run: pip install -r pipeline/requirements.txt

      - name: Run pipeline
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          NCBI_API_KEY: ${{ secrets.NCBI_API_KEY }}
          DB_HOST: localhost            # Individual DB vars
          DB_NAME: csvd_dashboard       # (not DATABASE_URL)
          DB_USER: csvd_user
          DB_PASSWORD: ${{ secrets.DB_PASSWORD }}
        run: python pipeline/main.py   # Direct script execution

      - name: Sync external data
        env:
          NCBI_API_KEY: ${{ secrets.NCBI_API_KEY }}
          DB_HOST: localhost
          DB_NAME: csvd_dashboard
          DB_USER: csvd_user
          DB_PASSWORD: ${{ secrets.DB_PASSWORD }}
        run: python pipeline/main.py --sync-external-data

      - name: Set up R
        uses: r-lib/actions/setup-r@v2

      - name: Install R dependencies
        run: |
          install.packages(c("DBI", "RPostgres", "pool", "qs"),
                           repos = "https://cloud.r-project.org")
        shell: Rscript {0}

      - name: Regenerate QS data from database
        env:
          DB_HOST: localhost
          DB_PORT: 5432
          DB_NAME: csvd_dashboard
          DB_USER: csvd_user
          DB_PASSWORD: ${{ secrets.DB_PASSWORD }}
        run: Rscript scripts/trigger_update.R

      - name: Commit QS data changes
        run: |
          git config --local user.email "action@github.com"
          git config --local user.name "GitHub Action"
          git add data/qs/
          git diff --quiet && git diff --staged --quiet || \
            git commit -m "Auto-update QS data: $(date +'%Y-%m-%d')"
          git push
```

> **Warning: Schedule Status**
>
> The cron schedule is currently commented out (disabled) for manual testing. Enable it by uncommenting the `schedule` block when the pipeline is ready for production use.

---

## Configuration

### Python Requirements

```bash
anthropic>=0.39.0
asyncpg>=0.29.0
biopython>=1.84
httpx>=0.27.0
pandas>=2.2.0
pydantic>=2.9.0
pdfplumber>=0.11.0
aiofiles>=24.1.0
python-dotenv>=1.0.0
tenacity>=9.0.0
```

### Environment Variables

#### Current Configuration

The R Shiny app uses a `.Renviron` file:

```bash
# PostgreSQL Database (used by R/utils.R with_db_connection())
PGUSER=csvd_user
PGPASSWORD=secure_password
```

#### Pipeline Configuration

The Python pipeline requires the following environment variables:

```bash
# API Keys (required)
ANTHROPIC_API_KEY=sk-ant-...   # Claude API key for LLM extraction
NCBI_API_KEY=...               # NCBI API key (optional, increases rate limits)

# Email addresses (required by external APIs)
ENTREZ_EMAIL=your@email.edu    # Required by NCBI Entrez policy
UNPAYWALL_EMAIL=your@email.edu # Required for open-access PDF lookup

# PostgreSQL Database (individual variables, NOT DATABASE_URL)
DB_HOST=localhost
DB_PORT=5432
DB_NAME=csvd_dashboard
DB_USER=csvd_user
DB_PASSWORD=secure_password
```

> **Note:** The pipeline uses individual database variables (`DB_HOST`, `DB_NAME`, etc.) rather than a single `DATABASE_URL`. This provides more flexibility for GitHub Actions secrets management.

### CLI Flags

The pipeline entry point (`python pipeline/main.py`) supports several command-line options:

```bash
python pipeline/main.py [OPTIONS]

Options:
  --days-back N         Number of days to look back for new papers (default: 7)
                        Range: 1 to 3650 (10 years)

  --dry-run             Run without writing to database. Useful for testing
                        search and extraction without modifying data.

  --test-mode           Skip LLM extraction entirely. Only tests PubMed search
                        and paper discovery. Shows which papers would be processed.

  --sync-external-data  Sync external data (NCBI, UniProt, PubMed) for all
                        genes in database. Populates cache tables for R
                        dashboard to read from. Does not process papers.

Examples:
  # Normal weekly run (paper processing)
  python pipeline/main.py

  # Look back 30 days for new papers
  python pipeline/main.py --days-back 30

  # Test run without database changes
  python pipeline/main.py --dry-run

  # Preview only - no LLM calls
  python pipeline/main.py --test-mode

  # Sync external data only (no paper processing)
  # This populates ncbi_gene_info, uniprot_info, and pubmed_citations tables
  python pipeline/main.py --sync-external-data
```

### Logging System

The pipeline writes timestamped log files to the `logs/` directory:

```python
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / f"pipeline_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),   # Console output
        logging.FileHandler(LOG_FILE),       # Timestamped log file
    ],
)
```

Each pipeline run creates a new log file (e.g., `logs/pipeline_2025-01-23_143052.log`) containing progress updates, validation results, and error messages.

### Rate Limiting and Concurrency

#### Claude API Rate Limiting

The pipeline implements exponential backoff for API rate limits:

| Constant | Value |
|----------|-------|
| `MAX_RETRIES` | 5 attempts |
| `BASE_RETRY_DELAY` | 60 seconds |
| `MAX_RETRY_DELAY` | 300 seconds (5 minutes) |
| `JITTER_FACTOR` | 0.25 (25% random variation) |

#### Paper Processing Concurrency

To stay within API rate limits (~30K tokens/minute), concurrent paper processing is limited:

```python
MAX_CONCURRENT_PAPERS: Final[int] = 2  # Low to avoid constant retries
semaphore = asyncio.Semaphore(MAX_CONCURRENT_PAPERS)
```

#### HTTP Client Pooling

Shared HTTP clients reduce connection overhead across multiple API calls:

```python
# Shared client for metadata fetching (pipeline/main.py)
_metadata_client = httpx.AsyncClient(
    timeout=httpx.Timeout(30.0),
    limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
)

# Shared client for PDF retrieval (pipeline/pdf_retrieval.py)
_http_client = httpx.AsyncClient(
    timeout=httpx.Timeout(60.0),
    limits=httpx.Limits(max_connections=10),
)
```

### Gene Validation Cache

The validation module maintains an async-safe cache for NCBI gene lookups to reduce API calls:

```python
# Thread-safe gene validation cache (pipeline/validation.py)
_gene_cache: dict[str, Optional[dict]] = {}  # symbol -> NCBI info

async def verify_ncbi_gene(symbol: str) -> Optional[dict]:
    """Query NCBI Gene with caching to avoid duplicate API calls."""
    if symbol in _gene_cache:
        return _gene_cache[symbol]

    # ... NCBI API call ...
    _gene_cache[symbol] = result
    return result

def clear_gene_cache() -> None:
    """Clear cache between pipeline runs."""
    _gene_cache.clear()
```

---

## Cost Estimate

| Component | Unit Cost | Monthly Volume | Monthly Cost |
|-----------|-----------|----------------|--------------|
| Claude Sonnet (extraction) | $3/1M input tokens | ~500K tokens | $1.50 |
| Claude Sonnet (output) | $15/1M output tokens | ~50K tokens | $0.75 |
| PubMed API | Free | -- | $0.00 |
| Unpaywall API | Free | -- | $0.00 |
| GitHub Actions | Free tier | ~30 min/week | $0.00 |
| PostgreSQL (managed) | $5-15/month | Small instance | $5-15 |
| **Total** | | | **$7-20/month** |
