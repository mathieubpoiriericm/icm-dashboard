# Pipeline Security

> Security considerations and mitigations for the SVD Dashboard ETL pipeline.

---

## Table of Contents

- [Threat Model Overview](#threat-model-overview)
- [LLM Prompt Injection](#llm-prompt-injection)
- [XML External Entity (XXE) Prevention](#xml-external-entity-xxe-prevention)
- [Secrets Management](#secrets-management)
- [Input Size Limits](#input-size-limits)
- [Container Hardening](#container-hardening)
- [Dependency Scanning](#dependency-scanning)

---

## Threat Model Overview

The pipeline ingests **untrusted external content** (academic papers, NCBI/Unpaywall XML responses) and processes it through LLM extraction, XML parsing, and database writes. The primary attack surfaces are:

| Surface | Threat | Risk | Mitigation |
| --- | --- | --- | --- |
| LLM extraction prompt | Prompt injection via paper text | Medium | 5-layer defense (see below) |
| NCBI XML responses | XXE / entity expansion | Medium | Hardened `lxml` parser |
| PDF downloads | Memory exhaustion | Low | 100 MB size cap |
| `.env` credentials | Credential exposure | High | `chmod 600` + gitignore |
| Container runtime | Privilege escalation | Medium | Non-root user + dropped caps |

---

## LLM Prompt Injection

Paper text is injected directly into the LLM extraction prompt. A malicious paper could theoretically attempt to manipulate extraction results. Five independent defense layers mitigate this risk:

```text
Paper Text
    |
    v
[1] Structured Outputs ---- constrained decoding to ExtractionResult schema
    |
    v
[2] Pydantic Validation --- typed fields, bounds checks, whitespace stripping
    |
    v
[3] Confidence Threshold -- reject entries below 0.6 confidence
    |
    v
[4] NCBI Gene Lookup ------ verify symbol exists in human genome
    |
    v
[5] Batch Anomaly Detection- flag statistical outliers across the run
    |
    v
Database
```

**Layer details:**

1. **Structured outputs (constrained decoding):** The LLM response is constrained to a JSON schema via `output_config` with `json_schema` format. The model cannot produce arbitrary text --- only valid JSON matching `ExtractionResult`.

2. **Pydantic validation:** Every `GeneEntry` is validated by Pydantic with typed fields, bounds-checked confidence scores, and whitespace stripping. Malformed entries are rejected before reaching the database.

3. **Confidence thresholding:** Entries below the configured `confidence_threshold` (default 0.6) are rejected during validation, filtering out low-quality or suspicious extractions.

4. **NCBI Gene validation:** Each extracted gene symbol is verified against the NCBI Gene database for *Homo sapiens*. Fabricated gene names are rejected.

5. **Batch anomaly detection:** `batch_validation.py` flags statistical anomalies across a pipeline run --- e.g., >20 genes per paper, >3 duplicated gene entries, mean confidence >0.95, or >30% null protein names.

### Monitoring Guidance

> [!IMPORTANT]
> Review these signals in pipeline logs after each run.

- Papers yielding unusually high gene counts (>20 per paper)
- Confidence score clustering at exactly 1.0 across many entries
- Batch validation warnings (logged at `WARNING` level)
- Unusual gene symbol clusters (many unknown genes in one paper) may indicate adversarial content

---

## XML External Entity (XXE) Prevention

All XML parsing of NCBI API responses uses a hardened parser that disables external entity resolution and network access:

```python
_SAFE_XML_PARSER = etree.XMLParser(resolve_entities=False, no_network=True)
root = etree.fromstring(content, parser=_SAFE_XML_PARSER)
```

While `lxml` disables external entities by default, explicit configuration provides defense-in-depth.

<details>
<summary>Files with hardened XML parsing</summary>

| File | Call Sites |
| --- | --- |
| `pipeline/pdf_retrieval.py` | `fetch_pmc_fulltext()`, `fetch_abstract()` |
| `pipeline/main.py` | `fetch_paper_metadata()` |
| `pipeline/pubmed_citations.py` | `_parse_pubmed_xml()` |

</details>

---

## Secrets Management

> [!CAUTION]
> `.env` contains API keys and database passwords. It **must not** be world-readable.

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

---

## Input Size Limits

Downloaded PDFs are capped at **100 MB** via `Content-Length` header check to prevent memory exhaustion from oversized or malicious files.

```python
MAX_PDF_BYTES = 100 * 1024 * 1024  # 100 MB
content_length = int(resp.headers.get("content-length", 0))
if content_length > MAX_PDF_BYTES:
    logger.warning(f"PDF too large ({content_length} bytes), skipping: {url}")
    return None
```

---

## Container Hardening

### Docker

| Control | Implementation |
| --- | --- |
| Non-root user | `USER shiny` in Dockerfile |
| Health monitoring | `HEALTHCHECK` with `curl` probe on `:3838` |
| Minimal packages | `--no-install-recommends` for apt |
| File ownership | `chown -R shiny:shiny` on app and log directories |

### Kubernetes

| Control | Implementation |
| --- | --- |
| Non-root enforcement | `runAsNonRoot: true` in Pod `securityContext` |
| Privilege escalation | `allowPrivilegeEscalation: false` |
| Capabilities | All capabilities dropped (`drop: [ALL]`) |
| Image pinning | Specific version tags (no `:latest`) |
| Resource limits | CPU and memory requests/limits on all containers |

---

## Dependency Scanning

No automated CI/CD pipeline exists yet. Run periodic vulnerability scans manually:

```bash
pip install pip-audit
pip-audit -r requirements.txt
```

> [!TIP]
> Consider adding `pip-audit` to a pre-commit hook or future CI step.
