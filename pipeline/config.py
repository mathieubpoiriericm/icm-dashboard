"""Centralized configuration for the SVD pipeline.

All tunable constants live here. Every setting can be overridden via
environment variable (prefixed with ``PIPELINE_``).  Modules accept a
``PipelineConfig`` instance instead of defining their own constants.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from lxml import etree  # type: ignore[import-untyped]


def _env_int(name: str, default: int) -> int:
    """Read an integer from an environment variable, falling back to *default*."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(
            f"Environment variable {name} must be an integer, got {raw!r}"
        ) from None


def _env_float(name: str, default: float) -> float:
    """Read a float from an environment variable, falling back to *default*."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        raise ValueError(
            f"Environment variable {name} must be a float, got {raw!r}"
        ) from None


def _env_str(name: str, default: str) -> str:
    """Read a string from an environment variable, falling back to *default*."""
    return os.getenv(name, default)


# Valid GWAS traits from cSVD literature (immutable reference data)
VALID_GWAS_TRAITS: Final[frozenset[str]] = frozenset(
    {
        "WMH",  # White matter hyperintensities
        "DWMH",  # Deep WMH
        "PVWMH",  # Periventricular WMH
        "SVS",  # Small vessel stroke
        "BG-PVS",  # Basal ganglia perivascular spaces
        "WM-PVS",  # White matter perivascular spaces
        "HIP-PVS",  # Hippocampal perivascular spaces
        "PSMD",  # Peak width of skeletonized mean diffusivity
        "MD",  # Mean diffusivity
        "extreme-cSVD",
        "FA",  # Fractional anisotropy
        "lacunes",
        "stroke",
        "cerebral-microbleeds",
        "ICH-lobar",  # Lobar intracerebral hemorrhage
        "ICH-non-lobar",  # Non-lobar intracerebral hemorrhage
        "DTI-ALPS",  # Glymphatic function marker
        "ICVF",  # Neurite density (NODDI)
        "ISOVF",  # Free-water volume fraction (NODDI)
        "OD",  # Orientation dispersion (NODDI)
        "WMH-cortical-atrophy",  # WMH-associated cortical atrophy
        "WM-BAG",  # White matter brain age gap
        "retinal-vessels",  # Retinal vessel phenotypes
    }
)

# Whitelist of allowed tables/columns for dynamic SQL (prevents SQL injection)
ALLOWED_TABLES: Final[frozenset[str]] = frozenset(
    {
        "genes",
        "pubmed_refs",
        "pipeline_runs",
        "ncbi_gene_info",
        "uniprot_info",
        "pubmed_citations",
    }
)
ALLOWED_COLUMNS: Final[frozenset[str]] = frozenset({"id"})

PMID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^\d{1,9}$")

# NCBI E-utilities base URLs
NCBI_ESEARCH_URL: Final[str] = (
    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
)
NCBI_ESUMMARY_URL: Final[str] = (
    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
)
NCBI_EFETCH_URL: Final[str] = (
    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
)

# Defense-in-depth: disable external entity resolution and network access
# to prevent XXE attacks when parsing untrusted XML from NCBI APIs.
SAFE_XML_PARSER: Final[etree.XMLParser] = etree.XMLParser(
    resolve_entities=False, no_network=True
)


def get_ncbi_params(base_params: dict[str, str]) -> dict[str, str]:
    """Add NCBI API key to params if available."""
    api_key = os.getenv("NCBI_API_KEY")
    if api_key:
        return {**base_params, "api_key": api_key}
    return base_params


def validate_pmid(pmid: str) -> str:
    """Validate and normalize a PubMed ID.

    Args:
        pmid: The PubMed identifier to validate.

    Returns:
        The validated PMID string.

    Raises:
        ValueError: If the PMID format is invalid.
    """
    pmid = pmid.strip()
    if not PMID_PATTERN.match(pmid):
        raise ValueError(f"Invalid PMID format: {pmid!r}")
    return pmid


PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

# Pricing per 1M tokens (input, output) — update when models change.
MODEL_PRICING: Final[dict[str, tuple[float, float]]] = {
    "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-5-20251101": (5.0, 25.0),
    "claude-opus-4-1-20250805": (15.0, 75.0),
    "claude-opus-4-20250514": (15.0, 75.0),
    "claude-sonnet-4-5-20250929": (3.0, 15.0),
    "claude-sonnet-4-20250514": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}


@dataclass
class PipelineConfig:
    """Centralised, immutable-ish configuration for the entire pipeline.

    Every field can be overridden via an environment variable. The naming
    convention is ``PIPELINE_<FIELD_UPPER>`` (e.g. ``PIPELINE_LLM_MODEL``).
    """

    # --- LLM settings ---
    llm_model: str = field(
        default_factory=lambda: _env_str("PIPELINE_LLM_MODEL", "claude-opus-4-6")
    )
    llm_max_tokens: int = field(
        default_factory=lambda: _env_int("PIPELINE_LLM_MAX_TOKENS", 64000)
    )
    # Effort level for adaptive thinking: "high" (default), "low", or "max".
    # Higher effort = deeper reasoning but more output tokens.
    llm_effort: str = field(
        default_factory=lambda: _env_str("PIPELINE_LLM_EFFORT", "high")
    )
    # Prompt version for A/B testing during tuning ("v1", "v2", etc.)
    prompt_version: str = field(
        default_factory=lambda: _env_str("PIPELINE_PROMPT_VERSION", "v5")
    )

    # Maximum paper text chars sent to the LLM (context-window buffer).
    max_paper_text_chars: int = field(
        default_factory=lambda: _env_int("PIPELINE_MAX_PAPER_TEXT_CHARS", 100_000)
    )

    # --- Retry settings (parse / API errors) ---
    max_retries: int = field(
        default_factory=lambda: _env_int("PIPELINE_MAX_RETRIES", 1)
    )
    retry_delay: float = field(
        default_factory=lambda: _env_float("PIPELINE_RETRY_DELAY", 2.0)
    )

    # --- Rate-limit retry settings (429 errors) ---
    max_rate_limit_retries: int = field(
        default_factory=lambda: _env_int("PIPELINE_MAX_RATE_LIMIT_RETRIES", 6)
    )
    rate_limit_retry_delay: float = field(
        default_factory=lambda: _env_float("PIPELINE_RATE_LIMIT_RETRY_DELAY", 1.0)
    )

    # --- Connection/network error retry settings ---
    max_connection_retries: int = field(
        default_factory=lambda: _env_int("PIPELINE_MAX_CONNECTION_RETRIES", 3)
    )
    connection_retry_delay: float = field(
        default_factory=lambda: _env_float("PIPELINE_CONNECTION_RETRY_DELAY", 2.0)
    )

    # --- Concurrency ---
    max_concurrent_papers: int = field(
        default_factory=lambda: _env_int("PIPELINE_MAX_CONCURRENT_PAPERS", 5)
    )

    # Estimated total tokens per LLM call (for rate limiter TPM tracking).
    # With Opus 4.6 adaptive thinking at high effort:
    # ~15K input + variable thinking + ~4K output.
    estimated_tokens_per_call: int = field(
        default_factory=lambda: _env_int("PIPELINE_ESTIMATED_TOKENS_PER_CALL", 40_000)
    )

    # --- Rate limiter (RPM / TPM) ---
    rpm_limit: int = field(default_factory=lambda: _env_int("PIPELINE_RPM_LIMIT", 50))
    tpm_limit: int = field(
        default_factory=lambda: _env_int("PIPELINE_TPM_LIMIT", 100_000)
    )

    # --- Validation ---
    confidence_threshold: float = field(
        default_factory=lambda: _env_float("PIPELINE_CONFIDENCE_THRESHOLD", 0.65)
    )

    # --- External API rate limits ---
    ncbi_rate_limit: int = field(
        default_factory=lambda: _env_int("PIPELINE_NCBI_RATE_LIMIT", 10)
    )
    uniprot_rate_limit: int = field(
        default_factory=lambda: _env_int("PIPELINE_UNIPROT_RATE_LIMIT", 5)
    )

    # --- Database ---
    db_pool_min_size: int = field(
        default_factory=lambda: _env_int("PIPELINE_DB_POOL_MIN", 2)
    )
    db_pool_max_size: int = field(
        default_factory=lambda: _env_int("PIPELINE_DB_POOL_MAX", 10)
    )
    db_command_timeout: float = field(
        default_factory=lambda: _env_float("PIPELINE_DB_COMMAND_TIMEOUT", 60.0)
    )

    # --- Pipeline range ---
    min_days_back: int = 1
    max_days_back: int = 365 * 10

    # --- Misc ---
    test_mode_preview_count: int = 10

    # --- Notifications (Apprise) ---
    notify_urls: str = field(
        default_factory=lambda: _env_str("PIPELINE_NOTIFY_URLS", "")
    )
    healthcheck_url: str = field(
        default_factory=lambda: _env_str("PIPELINE_HEALTHCHECK_URL", "")
    )
    event_db_path: str = field(
        default_factory=lambda: _env_str(
            "PIPELINE_EVENT_DB_PATH", str(PROJECT_ROOT / "logs" / "events.db")
        )
    )

    # --- Progress reporting ---
    progress_file: str = field(
        default_factory=lambda: _env_str(
            "PIPELINE_PROGRESS_FILE",
            str(PROJECT_ROOT / "data" / "pipeline_progress.json"),
        )
    )

    notify_max_retries: int = field(
        default_factory=lambda: _env_int("PIPELINE_NOTIFY_MAX_RETRIES", 3)
    )
    notify_retry_min_wait: float = field(
        default_factory=lambda: _env_float("PIPELINE_NOTIFY_RETRY_MIN_WAIT", 4.0)
    )
    notify_retry_max_wait: float = field(
        default_factory=lambda: _env_float("PIPELINE_NOTIFY_RETRY_MAX_WAIT", 30.0)
    )

    # --- Legacy email fields (deprecated — use PIPELINE_NOTIFY_URLS instead) ---
    email_host: str = field(default_factory=lambda: _env_str("PIPELINE_EMAIL_HOST", ""))
    email_port: int = field(
        default_factory=lambda: _env_int("PIPELINE_EMAIL_PORT", 587)
    )
    email_user: str = field(default_factory=lambda: _env_str("PIPELINE_EMAIL_USER", ""))
    email_password: str = field(
        default_factory=lambda: _env_str("PIPELINE_EMAIL_PASSWORD", "")
    )
    email_from: str = field(
        default_factory=lambda: _env_str(
            "PIPELINE_EMAIL_FROM", "noreply@svd-dashboard.org"
        )
    )
    email_admin: str = field(
        default_factory=lambda: _env_str("PIPELINE_EMAIL_ADMIN", "")
    )

    @property
    def model_version(self) -> str:
        """Extract short version from llm_model (e.g. 'claude-opus-4-6' -> '4.6')."""
        m = re.search(r"claude-(?:opus|sonnet|haiku)-(\d+)-(\d+)", self.llm_model)
        return f"{m.group(1)}.{m.group(2)}" if m else "unknown"
