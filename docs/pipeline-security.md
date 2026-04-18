# Pipeline Security

> Security considerations and mitigations for the SVD Dashboard ETL pipeline.

---

## Table of Contents

- [Threat Model Overview](#threat-model-overview)
- [Input Validation & Size Limits](#input-validation--size-limits)
- [LLM Prompt Injection](#llm-prompt-injection)
- [XML External Entity (XXE) Prevention](#xml-external-entity-xxe-prevention)
- [Database Security](#database-security)
- [HTTP Client Hardening](#http-client-hardening)
- [Secrets Management](#secrets-management)
- [Pipeline App Sandboxing](#pipeline-app-sandboxing)
- [Container Hardening](#container-hardening)
- [Dependency Scanning](#dependency-scanning)

---

## Threat Model Overview

The pipeline ingests **untrusted external content** (academic papers, NCBI/Unpaywall XML responses, PDF downloads) and processes it through LLM extraction, XML parsing, and database writes. The pipeline app (`pipeline_app/`) adds a local browser-driven interface that spawns subprocesses and reads files. The primary attack surfaces are:

| Surface | Threat | Risk | Mitigation |
| --- | --- | --- | --- |
| LLM extraction prompt | Prompt injection via paper text | Medium | 5-layer defense (see below) |
| NCBI / PubMed XML responses | XXE / entity expansion | Medium | Hardened `lxml` parser |
| PDF downloads | Memory exhaustion | Low | 100 MB dual-layer cap |
| User-supplied PMIDs / DOIs / paths | Malformed input | Low | Regex + filesystem validation |
| Database writes | SQL injection | Medium | Parameterised queries + identifier allowlist |
| `.env` credentials | Credential exposure | High | `chmod 600` + gitignore |
| Pipeline app subprocess | Env var / arg injection | Medium | Env allowlist + arg validation |
| Pipeline app file browser | Path traversal | Medium | `Path.relative_to()` confinement |
| Container runtime | Privilege escalation | Medium | Non-root user + dropped caps |

---

## Input Validation & Size Limits

External and user-supplied inputs are validated at the boundary before they enter the pipeline.

### PMID validation

PubMed IDs are matched against the regex `^\d{1,9}$` and stripped of whitespace before any downstream use:

```python
# pipeline/config.py
PMID_PATTERN = re.compile(r"^\d{1,9}$")

def validate_pmid(pmid: str) -> str:
    pmid = pmid.strip()
    if not PMID_PATTERN.match(pmid):
        raise ValueError(f"Invalid PMID format: {pmid!r}")
    return pmid
```

### DOI validation

DOIs are matched against `^10\.\d{4,}/[^\s]+$` before any Unpaywall lookup (`pipeline/pdf_retrieval.py:32`, `:77`).

### Paper text truncation

Paper text sent to the LLM is capped at `max_paper_text_chars` (default **100,000** characters) to bound the context window and limit how much untrusted text the model sees in a single request (`pipeline/config.py:196-198`).

### Local PDF inputs

When `--local-pdfs` is used, the path must exist and (for files) end with `.pdf`. An empty directory raises a hard error rather than silently doing nothing (`pipeline/main.py:837-847`).

### PDF download size cap (dual-layer)

Downloaded PDFs are capped at **100 MB** (`pipeline/pdf_retrieval.py:43`) using two independent checks so that responses without a `Content-Length` header (chunked transfers) are still bounded:

```python
# 1. Pre-check the Content-Length header (pipeline/pdf_retrieval.py:277-282)
content_length = int(resp.headers.get("content-length", 0))
if content_length > MAX_PDF_BYTES:
    logger.warning(f"PDF too large ({content_length} bytes), skipping: {url}")
    return None

# 2. Stream-and-accumulate guard (pipeline/pdf_retrieval.py:294-301)
total = 0
async for chunk in resp.aiter_bytes(65536):
    total += len(chunk)
    if total > MAX_PDF_BYTES:
        logger.warning(f"PDF exceeded {MAX_PDF_BYTES} bytes during download, skipping: {url}")
        return None
    chunks.append(chunk)
```

The body is also rejected if its `Content-Type` does not contain `pdf` and the URL does not end in `.pdf`.

---

## LLM Prompt Injection

Paper text is injected directly into the LLM extraction prompt. A malicious paper could theoretically attempt to manipulate extraction results. Five independent defense layers mitigate this risk:

```text
Paper Text
    |
    v
[1] Structured Outputs ----- constrained decoding to ExtractionResult schema
    |
    v
[2] Pydantic Validation ---- typed fields, bounds checks, whitespace stripping
    |
    v
[3] Confidence Threshold --- reject entries below 0.65 confidence
    |
    v
[4] NCBI Gene Lookup ------- verify symbol exists in human genome
    |
    v
[5] Batch Anomaly Detection- flag statistical outliers across the run
    |
    v
Database
```

**Layer details:**

1. **Structured outputs (constrained decoding)** — The LLM call sets `output_config["format"]["type"] = "json_schema"` with a schema derived from `ExtractionResult` (`pipeline/llm_extraction.py:79-84`). The model cannot return arbitrary text — only valid JSON matching the schema.

2. **Pydantic validation** — `GeneEntry` (`pipeline/llm_extraction.py:51-66`) sets `model_config["str_strip_whitespace"] = True` and bounds `confidence` with `ge=0.0, le=1.0`. Malformed entries are rejected before reaching downstream stages.

3. **Confidence thresholding** — Entries below `confidence_threshold` (default **0.65**, `pipeline/config.py:241-243`) are dropped at validation Stage 1 (`pipeline/validation.py:240-244`).

4. **NCBI Gene validation** — Each extracted symbol is queried against NCBI Gene with a *Homo sapiens* filter (`pipeline/validation.py:319`). Symbols not found are rejected; surviving symbols are normalised to the official NCBI symbol.

5. **Batch anomaly detection** — `pipeline/batch_validation.py` runs a Pandera schema check plus five batch-level heuristics:

| # | Check | Threshold | Location |
| --- | --- | --- | --- |
| 1 | Gene appears in unusually many papers | > 3 papers / batch | `:89-103` |
| 2 | Mean confidence suspiciously high | > 0.95 | `:105-113` |
| 3 | Null `protein_name` rate too high | > 30% | `:115-124` |
| 4 | Single paper yields too many genes | > 20 genes | `:126-135` |
| 5 | Suspiciously long causal-evidence summary | > 1000 chars | `:137-147` |

> [!IMPORTANT]
> Batch checks are currently **warning-only** while thresholds are tuned over a few production runs (see the module docstring). Promote to blocking once the false-positive rate is acceptable.

### Monitoring Guidance

Review these signals in pipeline logs after each run:

- Papers yielding unusually high gene counts (>20 per paper)
- Confidence score clustering at exactly 1.0 across many entries
- Batch validation warnings (logged at `WARNING` level with `Batch validation: N warning(s) raised`)
- Unusual gene symbol clusters (many unknown genes in one paper) may indicate adversarial content

---

## XML External Entity (XXE) Prevention

All XML parsing of NCBI / PubMed responses uses a single hardened `lxml` parser that disables external entity resolution and network access. The parser is defined once in `pipeline/config.py:107-109` and imported by every caller:

```python
# pipeline/config.py:107-109
SAFE_XML_PARSER: Final[etree.XMLParser] = etree.XMLParser(
    resolve_entities=False, no_network=True
)
```

While `lxml` disables external entities by default, explicit configuration provides defense-in-depth.

<details>
<summary>Call sites using SAFE_XML_PARSER</summary>

| File | Line | Function | Source |
| --- | --- | --- | --- |
| `pipeline/main.py` | 367 | `fetch_paper_metadata()` | NCBI efetch (PubMed metadata) |
| `pipeline/pubmed_citations.py` | 229 | `_parse_pubmed_xml()` | NCBI efetch (PubMed citations) |
| `pipeline/pdf_retrieval.py` | 217 | `fetch_pmc_fulltext()` | NCBI efetch (PMC full text) |
| `pipeline/pdf_retrieval.py` | 461 | `fetch_abstract()` | NCBI efetch (abstracts) |

</details>

`pipeline/ncbi_gene_fetch.py` and `pipeline/uniprot_fetch.py` consume JSON and TSV respectively and do not parse XML.

---

## Database Security

All database access goes through `pipeline/database.py` using `asyncpg` with a connection pool sized via `db_pool_min_size` / `db_pool_max_size` and a per-command timeout from `db_command_timeout`.

### Parameterised queries

Every query passes user- or pipeline-derived values as positional parameters (`$1`, `$2`, …) — never via string interpolation. The transactional merge is representative (`pipeline/database.py:176-264`):

```python
async with Database.connection() as conn, conn.transaction():
    await conn.executemany(
        """
            INSERT INTO genes (protein, gene, ...)
            VALUES ($1, $2, $3, ...)
            ON CONFLICT (gene) DO UPDATE SET ...
        """,
        [(g.get("protein"), g.get("gene"), ...) for g in to_insert],
    )
```

### Identifier allowlist + defense-in-depth quoting

When a query needs a dynamic table or column name (e.g., resetting a sequence), the names are checked against `ALLOWED_TABLES` / `ALLOWED_COLUMNS` allowlists *and* passed through PostgreSQL's `quote_ident` / `quote_literal` (`pipeline/database.py:140-174`):

```python
if table not in ALLOWED_TABLES:
    raise ValueError(f"Table '{table}' not in allowed list: {ALLOWED_TABLES}")
if column not in ALLOWED_COLUMNS:
    raise ValueError(f"Column '{column}' not in allowed list: {ALLOWED_COLUMNS}")

safe_table = await conn.fetchval("SELECT quote_ident($1)", table)
safe_column = await conn.fetchval("SELECT quote_ident($1)", column)
```

### Atomic merges

`merge_genes_transactional()` wraps the insert and update batches in a single `conn.transaction()` so a partial failure rolls back cleanly — preventing inconsistent state from a half-applied batch (`pipeline/database.py:195`).

---

## HTTP Client Hardening

External API calls go through a shared `AsyncHttpClientManager` singleton (`pipeline/http_client.py`) with explicit timeouts, connection caps, and rate limiting.

### Timeouts

Each httpx client is configured with explicit per-phase timeouts (no infinite waits):

| Client | Connect | Read | Write | Pool | Defined at |
| --- | --- | --- | --- | --- | --- |
| Default (NCBI / UniProt / validation) | 10 s | 30 s | 10 s | 5 s | `pipeline/pdf_retrieval.py:35-37` |
| PDF download | 10 s | 120 s | 10 s | 5 s | `pipeline/pdf_retrieval.py:38-40` |

### Connection limits

The shared HTTP client pool is capped to bound resource usage on slow APIs (`pipeline/pdf_retrieval.py:45-47`):

```python
DEFAULT_LIMITS: Final[httpx.Limits] = httpx.Limits(
    max_keepalive_connections=10, max_connections=20
)
```

### Rate limiting

`pipeline/rate_limiter.py` provides a token-bucket `AsyncRateLimiter` that gates Anthropic API calls on both **RPM** (default 50) and **TPM** (default 100,000) before the request fires, preventing 429s rather than reacting to them. `signal_rate_limit()` triggers a global backoff so all in-flight tasks pause together when any one of them does receive a 429 — preventing a thundering herd on retry.

NCBI calls are gated by a separate semaphore at `ncbi_rate_limit` requests/sec (default 10, `pipeline/config.py:246-248`).

---

## Secrets Management

> [!CAUTION]
> `.env` contains API keys and database passwords. It **must not** be world-readable.

### Pipeline secrets (`.env`, `.Renviron`)

| Control | Detail |
| --- | --- |
| Storage | `.env` (Python pipeline), `.Renviron` (R scripts) |
| Version control | Both files are gitignored |
| File permissions | `.env` requires `chmod 600`, `logs/` requires `chmod 700` |
| Log sanitization | No credentials are logged anywhere in the pipeline |
| Reference | See [`.env.example`](../.env.example) for variables and setup |

**Quick setup:**

```bash
cp .env.example .env
chmod 600 .env
chmod 700 logs/
# Fill in values in .env
```

### Pipeline app secret handling

The pipeline app (`pipeline_app/`) keeps a hard line between persisted config and credentials.

- `EnvSecrets` (`pipeline_app/config.py:110-122`) is loaded from `.env` only and is documented as "Never persisted by the app".
- `SENSITIVE_FIELDS` (`pipeline_app/config.py:26-38`) is the canonical frozenset of credential field names (`anthropic_api_key`, `db_password`, `ncbi_api_key`, etc.).
- `strip_secrets_from_config()` (`pipeline_app/config.py:360-362`) removes those keys from any config dict before it is written to a preset or history file.

This means saved presets, run history, and tuning history files in `pipeline_app/` cannot accidentally capture credentials even if a user clones a config that originally held them.

---

## Pipeline App Sandboxing

The pipeline app spawns subprocesses (`pipeline/main.py`, tuning scripts) and exposes a file browser. Both surfaces are constrained.

### Subprocess environment allowlist

`pipeline_app/runner.py:_base_env()` (`:176-185`) does **not** copy `os.environ` into the subprocess. Only an explicit allowlist passes through:

```python
def _base_env() -> dict[str, str]:
    env: dict[str, str] = {"PYTHONUNBUFFERED": "1"}
    for key in ("PATH", "HOME"):
        if key in os.environ:
            env[key] = os.environ[key]
    for key, val in os.environ.items():
        if key.startswith("SSL_CERT_"):
            env[key] = val
    return env
```

`build_env_vars()` (`pipeline_app/runner.py:188-232`) then injects only the explicit secrets and `PIPELINE_*` config keys the run actually needs. Arbitrary host environment leaks are blocked.

### CLI argument validation

CLI args built from UI state are validated before reaching the subprocess (`pipeline_app/runner.py:99-173`):

- `validate_python_path()` requires an executable matching the Python interpreter name regex.
- `validate_project_root()` requires a directory containing `pipeline/main.py` as a structural marker — preventing arbitrary-directory traversal via the project root field.
- `build_cli_args()` requires `days_back >= 1` and rejects `local_pdfs` / `pmid_list` modes with empty paths.

The subprocess is spawned with `start_new_session=True` to isolate the process group from the parent NiceGUI server.

### File browser path confinement

`pipeline_app/pages/file_browser.py` confines reads to the project's `logs/` directory regardless of the IDs sent over the WebSocket:

```python
# pipeline_app/pages/file_browser.py:19-30
def _is_within(path: Path, anchor: Path) -> bool:
    try:
        path.resolve().relative_to(anchor)
    except (OSError, ValueError):
        return False
    return True
```

`_is_within(path, resolved_logs)` is rechecked at every selection (`:127-129`) and at "Open in System App" (`:161-163`). Symlinks are skipped during the scan (`:62`), and the system-app opener uses the list form of `subprocess.Popen` with `start_new_session=True`, `stdin/stdout/stderr=DEVNULL`, and `close_fds=True` — no shell, no fd inheritance (`:182-189`).

---

## Container Hardening

### Docker

| Image | Base | User | Notes |
| --- | --- | --- | --- |
| `Dockerfile` (dashboard) | `rocker/shiny:4.5.2` (pinned) | `USER shiny` (`:59`) | `HEALTHCHECK` curl probe on `:3838` (`:63-64`); `--no-install-recommends` for apt; `chown -R shiny:shiny` on app and log dirs (`:54-57`) |
| `Dockerfile.pipeline` (pipeline) | `python:3.14-slim` (pinned) | `USER 65534` / nobody (`:42`) | kubectl install verified via `sha256sum --check` against the published checksum (`:31-35`); `chown -R 65534:65534 /app/logs` (`:39`) |

### Kubernetes (Helm chart at `helm/svd-dashboard/`)

**Dashboard `Deployment`** (`templates/dashboard-deployment.yaml`):

| Control | Implementation |
| --- | --- |
| Pod `securityContext` | `runAsNonRoot: true`, `runAsUser: 997`, `fsGroup: 997` (`:20-24`) |
| Container `securityContext` | `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]` (`:44-49`) |
| Root filesystem | `readOnlyRootFilesystem: false` — **intentional**: Shiny Server needs to write to `/var/lib/shiny-server` |
| Probes | startup / liveness / readiness on `/` (`:58-76`) |
| `.Renviron` mount | Mounted from a `Secret` and marked `readOnly: true` (`:54-57`, `:83-85`) |

The `fix-qs-permissions` initContainer runs as root by design (`runAsUser: 0`) to `chown` the shared PVC for the non-root dashboard process; it executes `busybox chown` and exits before the main container starts.

**Pipeline `CronJob`** (`templates/pipeline-cronjob.yaml`):

| Control | Implementation |
| --- | --- |
| Pod `securityContext` | `runAsNonRoot: true`, `runAsUser: 65534`, `seccompProfile.type: RuntimeDefault` (`:25-30`) |
| Container `securityContext` | `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]` on every step (`:36-40`, `:57-61`, `:78-82`, `:108-112`) |
| Concurrency | `concurrencyPolicy: Forbid` so a slow run cannot stack on top of itself (`:10`) |
| Job deadlines | `activeDeadlineSeconds` from values; `backoffLimit: 1` (`:15-16`) |
| Secrets | `envFrom: secretRef` for DB credentials and pipeline secrets (`:42-46`); per-key `secretKeyRef` for the R container (`:87-97`) |

**Image pinning:** All images in `values.yaml` are pinned to specific tags (`2.0.0` for dashboard and pipeline). No `:latest` references.

---

## Dependency Scanning

No automated CI/CD pipeline exists yet. Run periodic vulnerability scans manually:

```bash
pip install pip-audit
pip-audit -r requirements.txt
```

> [!TIP]
> Consider adding `pip-audit` to a pre-commit hook or future CI step.
