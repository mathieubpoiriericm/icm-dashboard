# SVD Dashboard Data Pipeline

An automated ETL pipeline for extracting gene data from PubMed literature related to cerebral small vessel disease (cSVD).

## Overview

This pipeline:

1. **Searches PubMed** for recent SVD-related genetic research papers
2. **Retrieves full text** from PubMed Central, Unpaywall, or abstracts as fallback
3. **Extracts genes** using Claude LLM with structured prompts
4. **Validates data** against NCBI Gene database
5. **Merges results** into PostgreSQL for the R Shiny dashboard

## Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│  pubmed_search  │───>│  pdf_retrieval   │───>│  llm_extraction │
│  (find papers)  │    │  (get full text) │    │  (extract genes)│
└─────────────────┘    └──────────────────┘    └─────────────────┘
                                                        │
                                                        v
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│    database     │<───│   data_merger    │<───│   validation    │
│   (PostgreSQL)  │    │ (transform/load) │    │ (NCBI verify)   │
└─────────────────┘    └──────────────────┘    └─────────────────┘
```

### Module Responsibilities

| Module | Purpose | External APIs |
|--------|---------|---------------|
| `main.py` | CLI entry point, orchestration | - |
| `pubmed_search.py` | PubMed literature discovery | NCBI Entrez |
| `pdf_retrieval.py` | Multi-source text retrieval | PMC, Unpaywall |
| `llm_extraction.py` | Structured gene extraction | Anthropic Claude |
| `validation.py` | Gene symbol verification | NCBI Gene |
| `data_merger.py` | Data transformation | - |
| `database.py` | Async PostgreSQL operations | PostgreSQL |
| `quality_metrics.py` | Pipeline statistics | - |

## Quick Start

### Prerequisites

- Python 3.14+
- PostgreSQL
- API keys for Anthropic and NCBI

### Installation

```bash
pip install anthropic asyncpg biopython httpx lxml pydantic python-dotenv pymupdf
```

### Environment Setup

Create a `.env` file in the project root:

```bash
# Anthropic API
ANTHROPIC_API_KEY=sk-ant-...

# NCBI Entrez API
NCBI_API_KEY=your_ncbi_key
ENTREZ_EMAIL=your_email@example.com

# Unpaywall API
UNPAYWALL_EMAIL=your_email@example.com

# PostgreSQL Database
DB_HOST=localhost
DB_PORT=5432
DB_NAME=csvd_dashboard
DB_USER=csvd_user
DB_PASSWORD=your_password
```

### Running the Pipeline

```bash
# Standard run (last 7 days)
python pipeline/main.py

# Extended lookback (30 days)
python pipeline/main.py --days-back 30

# Dry run (no database writes)
python pipeline/main.py --dry-run

# Test mode (skip LLM extraction and database merge)
python pipeline/main.py --test-mode
```

## Module Reference

### main.py

Central orchestrator with CLI interface.

**Functions:**
- `run_pipeline(days_back=7, dry_run=False, test_mode=False)` - Main async entry point
- `process_paper(pmid, metrics)` - Process individual paper through extraction/validation
- `fetch_paper_metadata(pmid)` - Fetch DOI from NCBI for a PMID

**CLI Arguments:**
| Flag | Default | Description |
|------|---------|-------------|
| `--days-back` | 7 | Days to look back for new papers |
| `--dry-run` | False | Skip database merge |
| `--test-mode` | False | Skip LLM extraction and database merge |

---

### pubmed_search.py

PubMed search using Biopython Entrez.

**Constants:**
- `DISEASE_TERMS` - cSVD/SVD terminology
- `MARKER_TERMS` - Clinical phenotypes (stroke, dementia, lacunes, WMH, etc.)
- `GENETIC_TERMS` - Research types (GWAS, EWAS, TWAS, PWAS, variants, etc.)

**Functions:**
- `search_recent_papers(days_back=7)` - Returns PMIDs matching SVD genetic criteria
- `filter_new_pmids(pmids, existing)` - Remove already-processed PMIDs

**Query Structure:**
```
((cSVD terms) AND (genetic terms)) OR ((marker terms) AND (cSVD terms))
```

---

### pdf_retrieval.py

Multi-strategy text retrieval with fallback chain.

**Retrieval Priority:**
1. PubMed Central (XML full text)
2. Unpaywall (open-access PDF)
3. PubMed abstract (fallback)

**Functions:**
- `get_fulltext(pmid, doi)` - Main entry point; tries sources in order
- `fetch_pmc_fulltext(pmid)` - Convert PMID to PMCID, fetch XML from PMC
- `check_unpaywall(doi)` - Query Unpaywall API for OA PDF URL
- `download_and_parse_pdf(url)` - Download and extract text using PyMuPDF
- `fetch_abstract(pmid)` - Fetch abstract from PubMed

**Return Format:**
```python
{"text": str, "source": str, "fulltext": bool}
```

**Timeout Configuration:**
- Default: connect=10s, read=30s, pool=5s
- PDF downloads: read=120s (extended for large files)

---

### llm_extraction.py

Claude-based gene extraction from paper text.

**Classes:**
- `GeneEntry` - Pydantic model with fields:
  - `gene_symbol` (required) - Official gene symbol
  - `protein_name` (optional) - Protein product name
  - `gwas_trait` (list) - Associated GWAS phenotypes
  - `mendelian_randomization` (bool) - MR evidence flag
  - `omics_evidence` (list) - TWAS/PWAS/EWAS results
  - `confidence` (required, 0.0-1.0) - Extraction confidence
  - `causal_evidence_summary` (optional) - Explanation of causal link

**Functions:**
- `extract_from_paper(text, pmid)` - Call Claude API (text truncated to 50k chars)
- `parse_llm_response(text)` - Parse JSON with fallback handling

**Model:** `claude-sonnet-4-20250514`

**Confidence Scoring:**
- 1.0: Direct causal evidence
- 0.7-0.9: Strong causal implication
- 0.4-0.6: Weak causal context
- <0.4: Tangential mention

**Rate Limit Handling:**
- 5 retries with exponential backoff
- Base delay: 60s, max delay: 300s
- Adds up to 25% random jitter to prevent thundering herd

---

### validation.py

Multi-stage gene validation.

**Classes:**
- `ValidationResult` - Contains `is_valid`, `errors`, `warnings`, `normalized_data`

**Validation Stages:**
1. **Confidence threshold** - Minimum 0.7 (`CONFIDENCE_THRESHOLD`)
2. **NCBI Gene verification** - Validates against NCBI, normalizes symbol
3. **GWAS trait validation** - Warns on unknown traits

**Valid GWAS Traits:**
`WMH`, `SVS`, `BG-PVS`, `WM-PVS`, `HIP-PVS`, `PSMD`, `extreme-cSVD`, `FA`, `lacunes`, `stroke`

**Functions:**
- `validate_gene_entry(entry)` - Run all validation stages
- `verify_ncbi_gene(symbol)` - Search NCBI Gene for human genes
- `fetch_gene_details(gene_id)` - Get symbol, description, chromosome, aliases

**Performance:**
- NCBI results cached with async-safe locking
- Rate limited to 10 concurrent NCBI requests

---

### data_merger.py

Transform and insert genes into PostgreSQL.

**Functions:**
- `merge_gene_entries(new_entries)` - Main merge logic, returns `{inserted, updated}`
- `format_omics(evidence)` - Format omics evidence with `*` suffix and `;` separators

**Field Mappings:**

| LLM Output | Database Column | Transformation |
|------------|-----------------|----------------|
| `gene_symbol` | `gene` | Direct |
| `protein_name` | `protein` | Falls back to gene_symbol |
| `gwas_trait[]` | `gwas_trait` | Join with ", " |
| `mendelian_randomization` | `mendelian_randomization` | Boolean to "Y" or "" |
| `omics_evidence[]` | `evidence_from_other_omics_studies` | Format as "TYPE:tissue*;" |
| `pmid` | `references` | Direct |

**Optimization:**
- Batch operations: 2 DB round-trips instead of N
- Fail-fast validation before any database writes

---

### database.py

Async PostgreSQL connection management.

**Classes:**
- `Database` - Singleton connection pool manager (2-10 connections)

**Functions:**
- `get_existing_genes()` - Returns set of all gene symbols
- `get_existing_pmids()` - Returns set of all processed PMIDs
- `reset_sequence(table, column="id")` - Reset sequence to avoid PK conflicts
- `insert_gene(gene_data)` - Insert with ON CONFLICT DO UPDATE
- `record_processed_pmid(pmid, fulltext_available, source, genes_extracted)` - Track processed PMIDs

**Database Tables:**

```sql
-- Main gene data
genes (
    protein, gene, chromosomal_location, gwas_trait,
    mendelian_randomization, evidence_from_other_omics_studies,
    link_to_monogenetic_disease, brain_cell_types,
    affected_pathway, references, updated_at
)

-- Processed PMID tracking
pubmed_refs (
    pmid, fulltext_available, source, genes_extracted, processed_at
)
```

---

### quality_metrics.py

Pipeline statistics tracking.

**Classes:**
- `PipelineMetrics` - Dataclass with fields:
  - `papers_processed` - Successfully processed papers
  - `fulltext_retrieved` - Papers with full text
  - `abstract_only` - Papers with abstract only
  - `genes_extracted` - Total genes extracted
  - `genes_validated` - Genes passing validation
  - `genes_rejected` - Genes rejected
  - `gene_acceptance_rate` (property) - Validated/extracted percentage

## Configuration

### Required Environment Variables

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Anthropic API key for Claude |
| `NCBI_API_KEY` | NCBI Entrez API key (increases rate limit to 10 req/sec) |
| `ENTREZ_EMAIL` | Email for NCBI Entrez API (required by NCBI policy) |
| `UNPAYWALL_EMAIL` | Email for Unpaywall API identification |
| `DB_HOST` | PostgreSQL host |
| `DB_PORT` | PostgreSQL port (default: 5432) |
| `DB_NAME` | Database name |
| `DB_USER` | Database username |
| `DB_PASSWORD` | Database password |

> **Note:** `ENTREZ_KEY` is accepted as an alias for `NCBI_API_KEY`.

### Hardcoded Constants

| Constant | File | Value | Purpose |
|----------|------|-------|---------|
| `CONFIDENCE_THRESHOLD` | validation.py | 0.7 | Minimum confidence for gene acceptance |
| `MAX_PAPER_TEXT_CHARS` | llm_extraction.py | 50,000 | Text truncation limit for Claude API |
| `MAX_CONCURRENT_PAPERS` | main.py | 2 | Parallel paper processing limit |
| `MAX_RETRIES` | llm_extraction.py | 5 | LLM API retry attempts |
| `NCBI_RATE_LIMIT` | validation.py | 10 | NCBI request semaphore limit |
| `valid_traits` | validation.py | (set) | Accepted GWAS trait values |

## Data Flow

### Input: PubMed Search

The search query combines disease terms with genetic research terms:
- Disease: "cerebral small vessel disease", "small vessel disease"
- Markers: stroke, dementia, lacunes, WMH, perivascular spaces, microbleeds
- Genetic: gene, genetic, GWAS, EWAS, TWAS, PWAS, genome-wide, variant, mutation

### Processing: Text Retrieval

Priority order with automatic fallback:
1. **PMC**: Convert PMID → PMCID, fetch XML, extract body paragraphs
2. **Unpaywall**: Query for OA PDF URL, download and parse with PyMuPDF
3. **Abstract**: Fetch from PubMed (handles structured abstracts)

### Output: Database Schema

Genes are inserted with UPSERT logic (ON CONFLICT DO UPDATE):
- Updates: gwas_trait, omics evidence, appends references
- Sets: updated_at timestamp

## Troubleshooting

### Common Issues

**"Gene not found in NCBI Gene"**
- Gene symbol may be an alias; validation auto-normalizes to official symbol
- Verify the gene exists in NCBI Gene for Homo sapiens

**"Low confidence" rejections**
- Threshold is 0.7; genes below this are rejected
- Review extraction if many valid genes are rejected

**Database connection errors**
- Verify PostgreSQL is running: `pg_isready -h localhost`
- Check credentials in `.env` file
- Ensure `genes` and `pubmed_refs` tables exist

**Rate limiting**
- Without `NCBI_API_KEY`: 3 requests/sec
- With `NCBI_API_KEY`: 10 requests/sec
- Unpaywall uses email for identification (not rate-limited)

### Logging

Logs are written to both console and timestamped files:
```
logs/pipeline_YYYY-MM-DD_HHMMSS.log
```

## Development Guide

### Adding New Extraction Fields

1. Update `GeneEntry` model in `llm_extraction.py`
2. Modify `EXTRACTION_PROMPT` to request the new field
3. Add validation logic in `validation.py` if needed
4. Update field mapping in `data_merger.py`
5. Add database column if required

### Testing

```bash
# Test search/retrieval only (no LLM costs)
python pipeline/main.py --test-mode --days-back 1

# Full pipeline without database writes
python pipeline/main.py --dry-run --days-back 1
```

### Code Patterns

- **Async/await**: All I/O operations use async
- **Type hints**: Required for all function signatures
- **Error handling**: Exceptions logged, pipeline continues on individual failures
- **Connection pooling**: Database uses asyncpg pool (2-10 connections)
