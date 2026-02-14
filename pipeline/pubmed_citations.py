"""PubMed citation fetching module.

Fetches citation details (authors, title, journal, DOI) from PubMed
and formats them for dashboard display.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Final

import httpx
from lxml import etree  # type: ignore[import-untyped]

from pipeline.config import PipelineConfig

logger = logging.getLogger(__name__)

# Defense-in-depth: disable external entity resolution and network access
# to prevent XXE attacks when parsing untrusted XML from NCBI APIs.
_SAFE_XML_PARSER: Final[etree.XMLParser] = etree.XMLParser(
    resolve_entities=False, no_network=True
)

NCBI_EFETCH_URL: Final[str] = (
    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
)


@dataclass(slots=True)
class PubMedCitation:
    """PubMed citation information."""

    pmid: str
    authors: str | None
    title: str | None
    journal: str | None
    publication_date: str | None
    doi: str | None
    formatted_ref: str


@dataclass(slots=True)
class SyncResult:
    """Result of sync operation."""

    fetched: int
    cached: int
    failed: int
    errors: list[str]


# Module-level shared HTTP client
_http_client: httpx.AsyncClient | None = None
_citation_cache: dict[str, PubMedCitation | None] = {}
_cache_lock = asyncio.Lock()
_ncbi_semaphore: asyncio.Semaphore | None = None


def _get_ncbi_semaphore(config: PipelineConfig | None = None) -> asyncio.Semaphore:
    """Get or create the NCBI rate-limit semaphore."""
    global _ncbi_semaphore
    if _ncbi_semaphore is None:
        limit = config.ncbi_rate_limit if config else PipelineConfig().ncbi_rate_limit
        _ncbi_semaphore = asyncio.Semaphore(limit)
    return _ncbi_semaphore


async def _get_http_client() -> httpx.AsyncClient:
    """Get or create shared HTTP client with connection pooling."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=30.0,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
    return _http_client


async def close_pubmed_client() -> None:
    """Close shared HTTP client (call at shutdown)."""
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


def clear_pubmed_cache() -> None:
    """Clear the citation cache."""
    global _citation_cache
    _citation_cache = {}


def _format_authors(author_list: list[str], max_authors: int = 3) -> str:
    """Format author list with 'et al.' for long lists."""
    if not author_list:
        return ""

    if len(author_list) <= max_authors:
        return ", ".join(author_list)

    return f"{', '.join(author_list[:max_authors])}, et al."


def _title_case(text: str) -> str:
    """Convert text to title case, handling special cases."""
    if not text:
        return ""

    # Simple title case - capitalize first letter of each sentence
    words = text.split()
    if words:
        words[0] = words[0].capitalize()
    return " ".join(words)


def _format_citation(
    authors: str | None,
    title: str | None,
    journal: str | None,
    pub_date: str | None,
    doi: str | None,
) -> str:
    """Format citation as HTML string for display.

    Format:
    Authors. Title. Journal (Date). DOI: doi
    """
    parts = []

    if authors:
        parts.append(f"<b>{authors}</b>")

    if title:
        # Title case the title
        formatted_title = _title_case(title)
        parts.append(f"<i>{formatted_title}</i>")

    journal_part = ""
    if journal:
        journal_part = journal
        if pub_date:
            journal_part += f" ({pub_date})"
        parts.append(journal_part)

    if doi:
        parts.append(f"DOI: {doi}")

    return "<br>".join(parts)


async def fetch_pubmed_citation(pmid: str) -> PubMedCitation | None:
    """Fetch citation details for a single PMID.

    Results are cached to avoid redundant API calls.

    Args:
        pmid: PubMed ID to look up.

    Returns:
        PubMedCitation if found, None on error.
    """
    pmid = pmid.strip()

    # Check cache first (without lock for read)
    if pmid in _citation_cache:
        return _citation_cache[pmid]

    async with _cache_lock:
        # Double-check after acquiring lock
        if pmid in _citation_cache:
            return _citation_cache[pmid]

        async with _get_ncbi_semaphore():
            result = await _fetch_pubmed_uncached(pmid)
            _citation_cache[pmid] = result
            return result


async def _fetch_pubmed_uncached(pmid: str) -> PubMedCitation | None:
    """Internal: fetch PubMed citation without caching."""
    params = {
        "db": "pubmed",
        "id": pmid,
        "retmode": "xml",
    }

    try:
        client = await _get_http_client()
        resp = await client.get(NCBI_EFETCH_URL, params=params)

        if resp.status_code != 200:
            logger.warning(f"PubMed efetch failed for PMID {pmid}: {resp.status_code}")
            return None

        return _parse_pubmed_xml(pmid, resp.content)

    except httpx.TimeoutException:
        logger.warning(f"Timeout fetching PubMed citation for PMID {pmid}")
    except httpx.RequestError as e:
        logger.warning(f"Request error fetching PubMed citation for PMID {pmid}: {e}")

    return None


def _parse_pubmed_xml(pmid: str, xml_content: bytes) -> PubMedCitation | None:
    """Parse PubMed XML response to extract citation details."""
    try:
        root = etree.fromstring(xml_content, parser=_SAFE_XML_PARSER)
        article = root.find(".//PubmedArticle")

        if article is None:
            logger.warning(f"No PubmedArticle found for PMID {pmid}")
            return PubMedCitation(
                pmid=pmid,
                authors=None,
                title=None,
                journal=None,
                publication_date=None,
                doi=None,
                formatted_ref=f"PMID: {pmid} (citation not available)",
            )

        # Extract authors
        author_list = []
        for author in article.findall(".//Author"):
            last_name = author.findtext("LastName", "")
            initials = author.findtext("Initials", "")
            if last_name:
                author_list.append(f"{last_name} {initials}".strip())

        authors = _format_authors(author_list)

        # Extract title
        title = article.findtext(".//ArticleTitle", "")

        # Extract journal
        journal = article.findtext(".//Journal/Title", "")
        if not journal:
            journal = article.findtext(".//Journal/ISOAbbreviation", "")

        # Extract publication date
        pub_date = None
        pub_date_elem = article.find(".//PubDate")
        if pub_date_elem is not None:
            year = pub_date_elem.findtext("Year", "")
            month = pub_date_elem.findtext("Month", "")
            if year:
                pub_date = f"{month} {year}".strip() if month else year

        # Extract DOI
        doi = None
        for article_id in article.findall(".//ArticleId"):
            if article_id.get("IdType") == "doi":
                doi = article_id.text
                break

        # Format the citation
        formatted_ref = _format_citation(authors, title, journal, pub_date, doi)

        return PubMedCitation(
            pmid=pmid,
            authors=authors or None,
            title=title or None,
            journal=journal or None,
            publication_date=pub_date,
            doi=doi,
            formatted_ref=formatted_ref,
        )

    except etree.XMLSyntaxError as e:
        logger.error(f"XML parsing failed for PMID {pmid}: {e}")
        return None


async def fetch_pubmed_citations_batch(
    pmids: list[str],
    progress_callback: Any | None = None,
) -> list[PubMedCitation]:
    """Fetch citations for multiple PMIDs.

    Args:
        pmids: List of PubMed IDs to fetch.
        progress_callback: Optional callback(current, total) for progress updates.

    Returns:
        List of PubMedCitation objects.
    """
    results: list[PubMedCitation] = []
    total = len(pmids)

    for i, pmid in enumerate(pmids):
        citation = await fetch_pubmed_citation(pmid)
        if citation is not None:
            results.append(citation)
        else:
            # Return placeholder for failed lookups
            results.append(
                PubMedCitation(
                    pmid=pmid,
                    authors=None,
                    title=None,
                    journal=None,
                    publication_date=None,
                    doi=None,
                    formatted_ref=f"PMID: {pmid} (citation fetch failed)",
                )
            )

        if progress_callback:
            progress_callback(i + 1, total)

    return results


def extract_pmids_from_text(text: str) -> list[str]:
    """Extract unique PMIDs from text containing references.

    Handles formats like:
    - "PMID: 12345678"
    - "12345678, 23456789"
    - Semicolon or comma separated lists

    Args:
        text: Text containing PMID references.

    Returns:
        List of unique PMIDs.
    """
    if not text:
        return []

    # Find all 7-8 digit numbers (typical PMID format)
    pmids = re.findall(r"\b(\d{7,8})\b", text)
    return list(dict.fromkeys(pmids))  # Preserve order, remove duplicates


async def sync_pubmed_citations(pmids: list[str]) -> SyncResult:
    """Sync PubMed citations to database for given PMIDs.

    Args:
        pmids: List of PubMed IDs to sync.

    Returns:
        SyncResult with counts of fetched, cached, and failed citations.
    """
    from pipeline.database import (
        get_cached_pubmed_citations,
        upsert_pubmed_citations_batch,
    )

    # Deduplicate PMIDs
    unique_pmids = list(dict.fromkeys(pmids))

    # Check what's already cached in database
    cached_citations = await get_cached_pubmed_citations(unique_pmids)
    pmids_to_fetch = [p for p in unique_pmids if p not in cached_citations]

    logger.info(
        f"PubMed sync: {len(cached_citations)} cached, {len(pmids_to_fetch)} to fetch"
    )

    if not pmids_to_fetch:
        return SyncResult(
            fetched=0,
            cached=len(cached_citations),
            failed=0,
            errors=[],
        )

    # Fetch missing citations
    def log_progress(current: int, total: int) -> None:
        if current % 10 == 0 or current == total:
            logger.info(f"  PubMed fetch progress: {current}/{total}")

    fetched_citations = await fetch_pubmed_citations_batch(pmids_to_fetch, log_progress)

    # Store in database - all citations are stored, even placeholder ones
    await upsert_pubmed_citations_batch(fetched_citations)

    # Count successful vs failed
    successful = [c for c in fetched_citations if c.title is not None]
    failed = [c for c in fetched_citations if c.title is None]

    return SyncResult(
        fetched=len(successful),
        cached=len(cached_citations),
        failed=len(failed),
        errors=[f"Citation fetch failed: PMID {c.pmid}" for c in failed],
    )
