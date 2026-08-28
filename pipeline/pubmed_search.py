"""PubMed search module for cSVD/SVD genetic research papers.

Uses NCBI Entrez API to search PubMed for recent publications.
Requires ENTREZ_EMAIL environment variable (NCBI policy).
"""

import asyncio
import logging
import os
import warnings
from datetime import UTC, datetime, timedelta
from functools import partial
from http.client import HTTPException
from typing import Any, Final, Protocol, TypedDict, cast
from urllib.error import HTTPError, URLError

from Bio import Entrez

logger = logging.getLogger(__name__)

# --- Configuration ---
_entrez_configured: bool = False


class _EntrezConfiguration(Protocol):
    """Writable Entrez globals whose upstream stubs are typed as ``None``."""

    email: str
    api_key: str | None


def _configure_entrez() -> None:
    """Configure Entrez API credentials (lazy initialization)."""
    global _entrez_configured
    if _entrez_configured:
        return

    email = os.getenv("ENTREZ_EMAIL", "")
    api_key = os.getenv("ENTREZ_KEY") or os.getenv("NCBI_API_KEY")

    if not email:
        warnings.warn(
            "ENTREZ_EMAIL not set. NCBI requires valid email for Entrez API. "
            "Set ENTREZ_EMAIL environment variable.",
            UserWarning,
            stacklevel=3,
        )

    entrez_config = cast(_EntrezConfiguration, Entrez)
    entrez_config.email = email
    entrez_config.api_key = api_key
    _entrez_configured = True


# --- Constants ---
MIN_DAYS_BACK: Final[int] = 1
MAX_DAYS_BACK: Final[int] = 365 * 10  # 10 years
DEFAULT_RETMAX: Final[int] = 500

# Primary disease terms for cSVD/SVD - canonical names used in literature
DISEASE_TERMS: Final[tuple[str, ...]] = ("cerebral small vessel disease",)

# cSVD imaging markers and clinical phenotypes
MARKER_TERMS: Final[tuple[str, ...]] = (
    "stroke",
    "dementia",
    "lacunes",
    "lacunar stroke",
    "white matter hyperintensities",
    "perivascular spaces",
    "cerebral microbleeds",
)

# Terms to capture genetic/omics research methodologies
GENETIC_TERMS: Final[tuple[str, ...]] = (
    "gene",
    "genetic",
    "GWAS",
    "EWAS",
    "TWAS",
    "PWAS",
    "genome-wide",
    "variant",
    "mutation",
    "polymorphism",
)


class PubMedSearchError(Exception):
    """Raised when PubMed search fails."""


class _EntrezSearchResult(TypedDict, total=False):
    """Subset of Entrez ``esearch`` fields consumed by this module."""

    IdList: list[str]
    Count: str | int
    WebEnv: str
    QueryKey: str


def _build_query() -> str:
    """Build the PubMed query for cSVD/SVD genetic research."""
    disease_clause = " OR ".join(f'"{t}"[Title/Abstract]' for t in DISEASE_TERMS)
    research_clause = " OR ".join(f'"{t}"[Title/Abstract]' for t in GENETIC_TERMS)
    main_query = f"(({disease_clause}) AND ({research_clause}))"

    marker_clause = " OR ".join(f'"{t}"[Title/Abstract]' for t in MARKER_TERMS)
    marker_query = f"(({marker_clause}) AND ({disease_clause}))"

    return f"{main_query} OR {marker_query}"


SVD_QUERY: Final[str] = _build_query()


MAX_TOTAL_RESULTS: Final[int] = 5000


async def _run_entrez_search(**params: Any) -> _EntrezSearchResult:
    """Run blocking Entrez search/read calls off the event loop."""
    handle = await asyncio.to_thread(partial(Entrez.esearch, **params))
    return cast(
        _EntrezSearchResult,
        await asyncio.to_thread(Entrez.read, handle),
    )


async def search_recent_papers(days_back: int = 7) -> list[str]:
    """Return PMIDs of papers published in the last N days.

    Uses asyncio.to_thread to avoid blocking the event loop with
    synchronous BioPython Entrez calls. Paginates through results
    using WebEnv/QueryKey when more than DEFAULT_RETMAX are available.

    Args:
        days_back: Number of days to look back (1 to 3650).

    Returns:
        List of PMID strings.

    Raises:
        ValueError: If days_back is not in valid range.
        PubMedSearchError: If Entrez API call fails.
    """
    _configure_entrez()

    if not MIN_DAYS_BACK <= days_back <= MAX_DAYS_BACK:
        raise ValueError(
            f"days_back must be between {MIN_DAYS_BACK} and {MAX_DAYS_BACK}, "
            f"got {days_back}"
        )

    # UTC so the window is stable regardless of host timezone and DST —
    # PubMed's index timestamps are UTC-based.
    mindate = (datetime.now(UTC) - timedelta(days=days_back)).strftime("%Y/%m/%d")

    logger.info(f"PubMed query (last {days_back}d): {SVD_QUERY[:120]}...")

    try:
        results = await _run_entrez_search(
            db="pubmed",
            term=SVD_QUERY,
            datetype="pdat",
            mindate=mindate,
            maxdate="3000",
            retmax=DEFAULT_RETMAX,
            usehistory="y",
        )
    except (URLError, HTTPError, HTTPException, OSError, RuntimeError) as e:
        raise PubMedSearchError(f"Entrez API call failed: {e}") from e

    pmids: list[str] = list(results.get("IdList", []))
    total_count = int(results.get("Count", 0))

    # Paginate if there are more results than the initial batch
    if total_count > DEFAULT_RETMAX:
        web_env = results.get("WebEnv")
        query_key = results.get("QueryKey")

        if web_env and query_key:
            while len(pmids) < total_count and len(pmids) < MAX_TOTAL_RESULTS:
                try:
                    batch = await _run_entrez_search(
                        db="pubmed",
                        term=SVD_QUERY,
                        retstart=len(pmids),
                        retmax=DEFAULT_RETMAX,
                        webenv=web_env,
                        query_key=query_key,
                    )
                except (URLError, HTTPError, HTTPException, OSError, RuntimeError) as e:
                    logger.warning(
                        f"PubMed pagination failed at offset {len(pmids)}: {e}"
                    )
                    logger.warning(
                        f"PubMed results TRUNCATED due to pagination error: "
                        f"retrieved {len(pmids)} of {total_count} total available. "
                        f"Some papers may be missing from this pipeline run."
                    )
                    break

                batch_ids = batch.get("IdList", [])
                if not batch_ids:
                    break
                pmids.extend(batch_ids)

            if len(pmids) >= MAX_TOTAL_RESULTS:
                logger.warning(
                    f"PubMed results capped at {MAX_TOTAL_RESULTS} "
                    f"(total available: {total_count})"
                )

    logger.info(f"PubMed search returned {len(pmids)} result(s)")
    return pmids


def filter_new_pmids(pmids: list[str], existing: set[str]) -> list[str]:
    """Remove PMIDs already in the dashboard (preserves order, dedupes).

    Args:
        pmids: List of PMIDs to filter.
        existing: Set of PMIDs already processed.

    Returns:
        List of new, unique PMIDs in original order.
    """
    return list(dict.fromkeys(pmid for pmid in pmids if pmid not in existing))
