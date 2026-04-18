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


def _env_bool(name: str, default: bool) -> bool:
    """Read a boolean from an environment variable, falling back to *default*.

    Truthy values (case-insensitive): "1", "true", "yes", "on".
    Anything else is falsy.
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """Read a comma-separated list from an environment variable.

    Empty entries are dropped; surrounding whitespace is stripped. Returns
    *default* if the variable is unset.
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    return tuple(item.strip() for item in raw.split(",") if item.strip())


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
        "clinical_trials",
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

# Default ClinicalTrials.gov condition/keyword terms for cSVD relevance.
# Override via PIPELINE_CT_SEARCH_TERMS (comma-separated).
DEFAULT_CT_SEARCH_TERMS: Final[tuple[str, ...]] = (
    "cerebral small vessel disease",
    "lacunar stroke",
    "lacunar infarction",
    "CADASIL",
    "CARASIL",
    "cerebral microbleeds",
    "white matter hyperintensities",
    "vascular cognitive impairment",
    "vascular dementia",
    "cerebral amyloid angiopathy",
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

# Models that support adaptive thinking (type: "adaptive").
# All other models require manual thinking (type: "enabled" + budget_tokens).
ADAPTIVE_THINKING_MODELS: Final[frozenset[str]] = frozenset(
    {"claude-opus-4-7", "claude-sonnet-4-6"}
)

# Effort parameter support currently coincides with adaptive thinking.
# Split into a separate frozenset if a future model breaks this invariant.
EFFORT_CAPABLE_MODELS: Final[frozenset[str]] = ADAPTIVE_THINKING_MODELS

# Tokens reserved for JSON response text when using manual thinking;
# the remainder of llm_max_tokens is available to the thinking block.
THINKING_OUTPUT_RESERVE: Final[int] = 8_000

# Maximum output tokens per model — from Anthropic API docs.
MODEL_MAX_OUTPUT_TOKENS: Final[dict[str, int]] = {
    "claude-opus-4-7": 128_000,
    "claude-sonnet-4-6": 64_000,
    "claude-haiku-4-5-20251001": 64_000,
}

# Pricing per 1M tokens (input, output) — update when models change.
MODEL_PRICING: Final[dict[str, tuple[float, float]]] = {
    "claude-opus-4-7": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
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
        default_factory=lambda: _env_str("PIPELINE_LLM_MODEL", "claude-opus-4-7")
    )
    # 0 = auto-resolve to model's maximum (see __post_init__).
    llm_max_tokens: int = field(
        default_factory=lambda: _env_int("PIPELINE_LLM_MAX_TOKENS", 0)
    )
    # Effort level: "high" (default), "low", "medium", "xhigh", or "max".
    # "xhigh" and "max" are Opus-tier only.
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
    # Rough pre-call budget (~15K input + variable thinking + ~4K text);
    # the rate limiter self-corrects via record_actual_usage() after
    # each call.
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

    # --- ClinicalTrials.gov sync ---
    ct_enabled: bool = field(
        default_factory=lambda: _env_bool("PIPELINE_CT_ENABLED", True)
    )
    ct_search_terms: tuple[str, ...] = field(
        default_factory=lambda: _env_list(
            "PIPELINE_CT_SEARCH_TERMS", DEFAULT_CT_SEARCH_TERMS
        )
    )
    ct_page_size: int = field(
        default_factory=lambda: _env_int("PIPELINE_CT_PAGE_SIZE", 100)
    )
    ct_max_concurrency: int = field(
        default_factory=lambda: _env_int("PIPELINE_CT_MAX_CONCURRENCY", 5)
    )
    ct_max_retries: int = field(
        default_factory=lambda: _env_int("PIPELINE_CT_MAX_RETRIES", 3)
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
        default_factory=lambda: (
            _env_str("PIPELINE_EVENT_DB_PATH", str(PROJECT_ROOT / "logs" / "events.db"))
            or str(PROJECT_ROOT / "logs" / "events.db")
        ),
    )

    # --- Progress reporting ---
    progress_file: str = field(
        default_factory=lambda: (
            _env_str(
                "PIPELINE_PROGRESS_FILE",
                str(PROJECT_ROOT / "logs" / "json" / "pipeline_progress.json"),
            )
            or str(PROJECT_ROOT / "logs" / "json" / "pipeline_progress.json")
        ),
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

    def __post_init__(self) -> None:
        if self.llm_max_tokens == 0:
            self.llm_max_tokens = MODEL_MAX_OUTPUT_TOKENS.get(self.llm_model, 64_000)

        # Fail fast on CT misconfiguration — Semaphore(0) would hang every
        # fetch until the outer 1-hour timeout, and negative values crash
        # deep inside an async call with no config context.
        if self.ct_max_concurrency < 1:
            raise ValueError(
                f"ct_max_concurrency must be >= 1, got {self.ct_max_concurrency}"
            )
        if self.ct_page_size < 1 or self.ct_page_size > 1000:
            raise ValueError(
                f"ct_page_size must be in [1, 1000], got {self.ct_page_size}"
            )
        if self.ct_max_retries < 0:
            raise ValueError(f"ct_max_retries must be >= 0, got {self.ct_max_retries}")

    @property
    def model_version(self) -> str:
        """Extract short version from llm_model (e.g. 'claude-opus-4-7' -> '4.7')."""
        m = re.search(r"claude-(?:opus|sonnet|haiku)-(\d+)-(\d+)", self.llm_model)
        return f"{m.group(1)}.{m.group(2)}" if m else "unknown"

    @property
    def thinking_mode(self) -> str:
        """Thinking-block mode used for this model: 'adaptive' or 'manual'."""
        return "adaptive" if self.llm_model in ADAPTIVE_THINKING_MODELS else "manual"
