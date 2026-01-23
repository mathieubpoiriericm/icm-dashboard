"""PDF and fulltext retrieval module with async HTTP client pooling.

This module provides efficient multi-source text fetching for academic papers,
supporting PubMed Central, Unpaywall, and abstract fallback.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Final, Literal, TypedDict

import httpx
from lxml import etree  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

# --- Constants ---
PMID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^\d{1,8}$")
DOI_PATTERN: Final[re.Pattern[str]] = re.compile(r"^10\.\d{4,}/[^\s]+$")

# Timeout configurations
DEFAULT_TIMEOUT: Final[httpx.Timeout] = httpx.Timeout(
    connect=10.0, read=30.0, write=10.0, pool=5.0
)
PDF_TIMEOUT: Final[httpx.Timeout] = httpx.Timeout(
    connect=10.0, read=120.0, write=10.0, pool=5.0
)

# Connection limits
DEFAULT_LIMITS: Final[httpx.Limits] = httpx.Limits(
    max_keepalive_connections=10, max_connections=20
)

# Environment config
UNPAYWALL_EMAIL: Final[str] = os.getenv("UNPAYWALL_EMAIL", "")
NCBI_API_KEY: Final[str | None] = os.getenv("NCBI_API_KEY")


class FulltextResult(TypedDict):
    """Result from fulltext retrieval attempt."""

    text: str | None
    source: Literal["pmc", "unpaywall", "abstract"]
    fulltext: bool


# --- Module-level shared HTTP client ---
_http_client: httpx.AsyncClient | None = None


async def _get_http_client() -> httpx.AsyncClient:
    """Get or create shared HTTP client with connection pooling."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT,
            follow_redirects=True,
            limits=DEFAULT_LIMITS,
        )
    return _http_client


async def close_http_client() -> None:
    """Close shared HTTP client (call at shutdown)."""
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


def _validate_pmid(pmid: str) -> str:
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


def _validate_doi(doi: str) -> str:
    """Validate and normalize a DOI.

    Args:
        doi: The DOI to validate.

    Returns:
        The validated DOI string.

    Raises:
        ValueError: If the DOI format is invalid.
    """
    doi = doi.strip()
    if not DOI_PATTERN.match(doi):
        raise ValueError(f"Invalid DOI format: {doi!r}")
    return doi


def _get_ncbi_params(base_params: dict[str, str]) -> dict[str, str]:
    """Add NCBI API key to params if available."""
    if NCBI_API_KEY:
        return {**base_params, "api_key": NCBI_API_KEY}
    return base_params


async def get_fulltext(pmid: str, doi: str | None) -> FulltextResult:
    """Attempt full-text retrieval from multiple sources.

    Tries sources in order: PMC -> Unpaywall -> Abstract fallback.

    Args:
        pmid: PubMed ID of the paper.
        doi: Digital Object Identifier (optional).

    Returns:
        FulltextResult with text content and source information.
    """
    pmid = _validate_pmid(pmid)

    # Try PubMed Central first
    if pmc_text := await fetch_pmc_fulltext(pmid):
        return {"text": pmc_text, "source": "pmc", "fulltext": True}

    # Try Unpaywall for OA PDF
    if doi:
        try:
            doi = _validate_doi(doi)
            if oa_url := await check_unpaywall(doi):
                if pdf_text := await download_and_parse_pdf(oa_url):
                    return {"text": pdf_text, "source": "unpaywall", "fulltext": True}
        except ValueError:
            logger.debug(f"Invalid DOI format for PMID {pmid}: {doi}")

    # Fallback to abstract only
    abstract = await fetch_abstract(pmid)
    return {"text": abstract, "source": "abstract", "fulltext": False}


async def check_unpaywall(doi: str) -> str | None:
    """Query Unpaywall API for open-access PDF URL.

    Args:
        doi: Digital Object Identifier.

    Returns:
        URL to PDF if available, None otherwise.
    """
    if not UNPAYWALL_EMAIL:
        logger.warning("UNPAYWALL_EMAIL not set, skipping Unpaywall lookup")
        return None

    url = f"https://api.unpaywall.org/v2/{doi}"
    params = {"email": UNPAYWALL_EMAIL}

    try:
        client = await _get_http_client()
        resp = await client.get(url, params=params)

        match resp.status_code:
            case 200:
                data = resp.json()
                if data.get("is_oa") and data.get("best_oa_location"):
                    return data["best_oa_location"].get("url_for_pdf")
            case 404:
                logger.debug(f"DOI not found in Unpaywall: {doi}")
            case 429:
                logger.warning("Unpaywall rate limit exceeded")
            case status:
                logger.debug(f"Unpaywall returned status {status} for DOI {doi}")

    except httpx.TimeoutException:
        logger.warning(f"Timeout checking Unpaywall for DOI {doi}")
    except httpx.RequestError as e:
        logger.warning(f"Request error checking Unpaywall for DOI {doi}: {e}")

    return None


async def fetch_pmc_fulltext(pmid: str) -> str | None:
    """Fetch full text from PubMed Central.

    First checks if the PMID has a corresponding PMC article,
    then fetches the full text in XML format and extracts the body.

    Args:
        pmid: PubMed ID.

    Returns:
        Full text content if available, None otherwise.
    """
    # Step 1: Convert PMID to PMCID
    convert_url = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"
    params = {"ids": pmid, "format": "json"}

    try:
        client = await _get_http_client()
        resp = await client.get(convert_url, params=params)

        if resp.status_code != 200:
            logger.debug(f"PMC ID conversion failed for PMID {pmid}: {resp.status_code}")
            return None

        data = resp.json()
        records = data.get("records", [])
        if not records or "pmcid" not in records[0]:
            return None  # No PMC article for this PMID

        pmcid = records[0]["pmcid"]

        # Step 2: Fetch full text from PMC
        pmc_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
        pmc_params = _get_ncbi_params({"db": "pmc", "id": pmcid, "rettype": "xml"})

        pmc_resp = await client.get(pmc_url, params=pmc_params)

        if pmc_resp.status_code != 200:
            logger.debug(f"PMC fetch failed for {pmcid}: {pmc_resp.status_code}")
            return None

        # Parse XML and extract body text
        root = etree.fromstring(pmc_resp.content)

        # Extract all paragraph text from the body
        paragraphs = root.findall(".//body//p")
        if not paragraphs:
            paragraphs = root.findall(".//sec//p")

        if not paragraphs:
            return None

        text_parts = []
        for p in paragraphs:
            text = "".join(p.itertext())
            if text.strip():
                text_parts.append(text.strip())

        return "\n\n".join(text_parts) if text_parts else None

    except httpx.TimeoutException:
        logger.warning(f"Timeout fetching PMC fulltext for PMID {pmid}")
        return None
    except httpx.RequestError as e:
        logger.warning(f"Request error fetching PMC fulltext for PMID {pmid}: {e}")
        return None
    except etree.XMLSyntaxError as e:
        logger.error(f"XML parsing failed for PMID {pmid}: {e}")
        return None


async def download_and_parse_pdf(url: str) -> str | None:
    """Download PDF from URL and extract text using PyMuPDF (fitz).

    Args:
        url: URL to the PDF file.

    Returns:
        Extracted text content if successful, None otherwise.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.debug("PyMuPDF not available, skipping PDF extraction")
        return None

    try:
        # Use longer timeout for PDF downloads
        client = await _get_http_client()
        resp = await client.get(url, timeout=PDF_TIMEOUT)

        if resp.status_code != 200:
            logger.debug(f"PDF download failed: {resp.status_code} for {url}")
            return None

        # Check content type
        content_type = resp.headers.get("content-type", "")
        if "pdf" not in content_type.lower() and not url.endswith(".pdf"):
            logger.debug(f"Not a PDF (content-type: {content_type}): {url}")
            return None

        # Parse PDF from bytes
        pdf_bytes = resp.content
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")

        text_parts = []
        for page_num in range(len(doc)):
            page = doc[page_num]
            text = page.get_text()
            if text.strip():
                text_parts.append(text.strip())

        doc.close()

        return "\n\n".join(text_parts) if text_parts else None

    except httpx.TimeoutException:
        logger.warning(f"Timeout downloading PDF from {url}")
        return None
    except httpx.RequestError as e:
        logger.warning(f"Request error downloading PDF from {url}: {e}")
        return None
    except Exception as e:
        # PyMuPDF can raise various exceptions for corrupt PDFs
        logger.warning(f"PDF parsing failed for {url}: {e}")
        return None


async def fetch_abstract(pmid: str) -> str | None:
    """Fetch abstract for a given PMID from PubMed.

    Args:
        pmid: PubMed ID.

    Returns:
        Abstract text if available, None otherwise.
    """
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    params = _get_ncbi_params({
        "db": "pubmed",
        "id": pmid,
        "rettype": "abstract",
        "retmode": "xml",
    })

    try:
        client = await _get_http_client()
        resp = await client.get(url, params=params)

        if resp.status_code != 200:
            logger.debug(f"Abstract fetch failed for PMID {pmid}: {resp.status_code}")
            return None

        root = etree.fromstring(resp.content)

        # Find abstract text
        abstract_elem = root.find(".//AbstractText")
        if abstract_elem is not None:
            abstract_parts = root.findall(".//AbstractText")
            if len(abstract_parts) > 1:
                # Structured abstract with multiple sections
                sections = []
                for part in abstract_parts:
                    label = part.get("Label", "")
                    text = "".join(part.itertext())
                    if label:
                        sections.append(f"{label}: {text}")
                    else:
                        sections.append(text)
                return "\n\n".join(sections)
            else:
                return "".join(abstract_elem.itertext())

        return None

    except httpx.TimeoutException:
        logger.warning(f"Timeout fetching abstract for PMID {pmid}")
        return None
    except httpx.RequestError as e:
        logger.warning(f"Request error fetching abstract for PMID {pmid}: {e}")
        return None
    except etree.XMLSyntaxError as e:
        logger.error(f"XML parsing failed for abstract PMID {pmid}: {e}")
        return None
