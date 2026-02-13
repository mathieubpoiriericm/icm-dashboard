"""Centralized configuration for the SVD pipeline.

All tunable constants live here. Every setting can be overridden via
environment variable (prefixed with ``PIPELINE_``).  Modules accept a
``PipelineConfig`` instance instead of defining their own constants.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final


def _env_int(name: str, default: int) -> int:
    """Read an integer from an environment variable, falling back to *default*."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    """Read a float from an environment variable, falling back to *default*."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return float(raw)


def _env_str(name: str, default: str) -> str:
    """Read a string from an environment variable, falling back to *default*."""
    return os.getenv(name, default)


# Valid GWAS traits from cSVD literature (immutable reference data)
VALID_GWAS_TRAITS: Final[frozenset[str]] = frozenset(
    {
        "WMH",  # White matter hyperintensities
        "SVS",  # Small vessel stroke
        "BG-PVS",  # Basal ganglia perivascular spaces
        "WM-PVS",  # White matter perivascular spaces
        "HIP-PVS",  # Hippocampal perivascular spaces
        "PSMD",  # Peak width of skeletonized mean diffusivity
        "extreme-cSVD",
        "FA",  # Fractional anisotropy
        "lacunes",
        "stroke",
    }
)

# Whitelist of allowed tables/columns for dynamic SQL (prevents SQL injection)
ALLOWED_TABLES: Final[frozenset[str]] = frozenset(
    {
        "genes",
        "pubmed_refs",
        "ncbi_gene_info",
        "uniprot_info",
        "pubmed_citations",
    }
)
ALLOWED_COLUMNS: Final[frozenset[str]] = frozenset({"id"})

PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parent.parent


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
        default_factory=lambda: _env_int("PIPELINE_LLM_MAX_TOKENS", 32000)
    )
    # Effort level for adaptive thinking: "high" (default), "low", or "max".
    # Higher effort = deeper reasoning but more output tokens.
    llm_effort: str = field(
        default_factory=lambda: _env_str("PIPELINE_LLM_EFFORT", "high")
    )

    # Maximum paper text chars sent to the LLM (context-window buffer).
    max_paper_text_chars: int = field(
        default_factory=lambda: _env_int("PIPELINE_MAX_PAPER_TEXT_CHARS", 50_000)
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
        default_factory=lambda: _env_float("PIPELINE_CONFIDENCE_THRESHOLD", 0.7)
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
