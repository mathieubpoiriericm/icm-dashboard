"""PDF and fulltext retrieval module with async HTTP client pooling.

This module provides efficient multi-source text fetching for academic papers,
supporting PubMed Central, Unpaywall, and abstract fallback.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Any, Final, Literal, TypedDict

import httpx
from lxml import etree  # type: ignore[import-untyped]

from pipeline.config import (
    NCBI_EFETCH_URL,
    SAFE_XML_PARSER,
    get_ncbi_params,
    validate_pmid,
)
from pipeline.http_client import AsyncHttpClientManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CONSTANTS
# ---------------------------------------------------------------------------

DOI_PATTERN: Final[re.Pattern[str]] = re.compile(r"^10\.\d{4,}/[^\s]+$")
_BACK_MATTER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\n(?:References|Bibliography|Methods|Online content|Acknowledgements|"
    r"Data availability)\n",
    re.IGNORECASE,
)
_PDF_TOP_MARGIN: Final[int] = 40
_PDF_BOTTOM_MARGIN: Final[int] = 740

# Timeout configurations
DEFAULT_TIMEOUT: Final[httpx.Timeout] = httpx.Timeout(
    connect=10.0, read=30.0, write=10.0, pool=5.0
)
PDF_TIMEOUT: Final[httpx.Timeout] = httpx.Timeout(
    connect=10.0, read=120.0, write=10.0, pool=5.0
)

# Connection limits
MAX_PDF_BYTES: Final[int] = 100 * 1024 * 1024  # 100 MB

DEFAULT_LIMITS: Final[httpx.Limits] = httpx.Limits(
    max_keepalive_connections=10, max_connections=20
)

# Environment config
UNPAYWALL_EMAIL: Final[str] = os.getenv("UNPAYWALL_EMAIL", "")


class FulltextResult(TypedDict):
    """Result from fulltext retrieval attempt."""

    text: str | None
    source: Literal["pmc", "unpaywall", "abstract"]
    fulltext: bool


# ---------------------------------------------------------------------------
# HTTP CLIENT
# ---------------------------------------------------------------------------

_client_manager = AsyncHttpClientManager(
    timeout=DEFAULT_TIMEOUT,
    limits=DEFAULT_LIMITS,
    follow_redirects=True,
)


async def close_http_client() -> None:
    """Close shared HTTP client (call at shutdown)."""
    await _client_manager.close()


def _unpaywall_oa_url(data: dict[str, Any]) -> str | None:
    """Pick a best-available OA URL from an Unpaywall v2 payload, if any.

    Falls back to the landing page ``url`` when ``url_for_pdf`` is null
    (common for HTML-only OA locations); the PDF magic-byte check downstream
    rejects non-PDF responses.
    """
    if not (data.get("is_oa") and data.get("best_oa_location")):
        return None
    loc = data["best_oa_location"]
    return loc.get("url_for_pdf") or loc.get("url")


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
    if not DOI_PATTERN.fullmatch(doi):
        raise ValueError(f"Invalid DOI format: {doi!r}")
    return doi


def _element_text(element: Any) -> str:
    """Join the string fragments yielded by an lxml element."""
    return "".join(part for part in element.itertext() if isinstance(part, str))


def _parse_pmc_xml(content: bytes) -> str | None:
    """Extract body paragraphs from a PMC JATS document."""
    root = etree.fromstring(content, parser=SAFE_XML_PARSER)
    paragraphs = root.findall(".//{*}body//{*}p") or root.findall(".//{*}sec//{*}p")
    text_parts = [
        text for paragraph in paragraphs if (text := _element_text(paragraph).strip())
    ]
    return "\n\n".join(text_parts) if text_parts else None


def _parse_abstract_xml(content: bytes) -> str | None:
    """Extract either a plain or structured abstract from PubMed XML."""
    root = etree.fromstring(content, parser=SAFE_XML_PARSER)
    abstract_parts = root.findall(".//AbstractText")
    if not abstract_parts:
        return None
    if len(abstract_parts) == 1:
        return _element_text(abstract_parts[0])

    sections: list[str] = []
    for part in abstract_parts:
        text = _element_text(part).strip()
        if text:
            label = part.get("Label", "")
            sections.append(f"{label}: {text}" if label else text)
    return "\n\n".join(sections) if sections else None


async def _read_pdf_bytes(response: httpx.Response, url: str) -> bytes | None:
    """Validate and read a streamed PDF response within the size limit."""
    if response.status_code != 200:
        logger.debug(f"PDF download failed: {response.status_code} for {url}")
        return None

    raw_content_length = response.headers.get("content-length", "0")
    try:
        content_length = int(raw_content_length)
    except ValueError:
        logger.debug(
            "Non-numeric content-length %r for %s; relying on streaming guard",
            raw_content_length,
            url,
        )
        content_length = 0
    if content_length > MAX_PDF_BYTES:
        logger.warning(f"PDF too large ({content_length} bytes), skipping: {url}")
        return None

    content_type = response.headers.get("content-type", "")
    if "pdf" not in content_type.lower() and not url.endswith(".pdf"):
        logger.debug(f"Not a PDF (content-type: {content_type}): {url}")
        return None

    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes(65536):
        total += len(chunk)
        if total > MAX_PDF_BYTES:
            logger.warning(
                f"PDF exceeded {MAX_PDF_BYTES} bytes during download, skipping: {url}"
            )
            return None
        chunks.append(chunk)

    content = b"".join(chunks)
    if not content.startswith(b"%PDF-"):
        logger.debug(
            f"Response for {url} passed content-type check but is not a "
            f"PDF (first bytes: {content[:16]!r})"
        )
        return None
    return content


def _extract_and_close_pdf(doc: Any) -> str | None:
    """Extract cleaned text and always release the PyMuPDF document."""
    try:
        text = _extract_clean_pdf_text(doc)
    finally:
        doc.close()
    return text if text.strip() else None


# ---------------------------------------------------------------------------
# FULLTEXT RETRIEVAL
# ---------------------------------------------------------------------------


async def get_fulltext(pmid: str, doi: str | None) -> FulltextResult:
    """Attempt full-text retrieval from multiple sources.

    Tries sources in order: PMC -> Unpaywall -> Abstract fallback.

    Args:
        pmid: PubMed ID of the paper.
        doi: Digital Object Identifier (optional).

    Returns:
        FulltextResult with text content and source information.
    """
    pmid = validate_pmid(pmid)

    # Try PubMed Central first
    if pmc_text := await fetch_pmc_fulltext(pmid):
        return {"text": pmc_text, "source": "pmc", "fulltext": True}

    # Try Unpaywall for OA PDF
    if doi:
        try:
            doi = _validate_doi(doi)
            if (oa_url := await check_unpaywall(doi)) and (
                pdf_text := await download_and_parse_pdf(oa_url)
            ):
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
        client = await _client_manager.get()
        resp = await client.get(url, params=params)

        match resp.status_code:
            case 200:
                return _unpaywall_oa_url(resp.json())
            case 404:
                logger.debug(f"DOI not found in Unpaywall: {doi}")
            case 429:
                # Single retry — Unpaywall isn't critical-path; one attempt
                # is enough before falling back to abstract-only extraction.
                logger.warning("Unpaywall rate limit exceeded, retrying once in 2s")
                await asyncio.sleep(2.0)
                resp = await client.get(url, params=params)
                if resp.status_code == 200:
                    return _unpaywall_oa_url(resp.json())
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
    params = get_ncbi_params({"ids": pmid, "format": "json"})

    try:
        client = await _client_manager.get()
        resp = await client.get(convert_url, params=params)

        if resp.status_code != 200:
            logger.debug(
                f"PMC ID conversion failed for PMID {pmid}: {resp.status_code}"
            )
            return None

        data = resp.json()
        records = data.get("records", [])
        if not records or "pmcid" not in records[0]:
            return None  # No PMC article for this PMID

        pmcid = records[0]["pmcid"]

        # Step 2: Fetch full text from PMC
        pmc_params = get_ncbi_params({"db": "pmc", "id": pmcid, "rettype": "xml"})

        pmc_resp = await client.get(NCBI_EFETCH_URL, params=pmc_params)

        if pmc_resp.status_code != 200:
            logger.debug(f"PMC fetch failed for {pmcid}: {pmc_resp.status_code}")
            return None

        return _parse_pmc_xml(pmc_resp.content)

    except httpx.TimeoutException:
        logger.warning(f"Timeout fetching PMC fulltext for PMID {pmid}")
    except httpx.RequestError as e:
        logger.warning(f"Request error fetching PMC fulltext for PMID {pmid}: {e}")
    except etree.XMLSyntaxError as e:
        logger.error(f"XML parsing failed for PMID {pmid}: {e}")

    return None


# ---------------------------------------------------------------------------
# PDF PARSING
# ---------------------------------------------------------------------------


async def download_and_parse_pdf(url: str) -> str | None:
    """Download PDF from URL and extract cleaned text using PyMuPDF (fitz).

    Args:
        url: URL to the PDF file.

    Returns:
        Extracted and cleaned text content if successful, None otherwise.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.debug("PyMuPDF not available, skipping PDF extraction")
        return None

    try:
        client = await _client_manager.get()

        # Stream the response to avoid loading oversized PDFs into memory
        async with client.stream("GET", url, timeout=PDF_TIMEOUT) as resp:
            pdf_bytes = await _read_pdf_bytes(resp, url)
        if pdf_bytes is None:
            return None

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        return _extract_and_close_pdf(doc)

    except httpx.TimeoutException:
        logger.warning(f"Timeout downloading PDF from {url}")
    except httpx.RequestError as e:
        logger.warning(f"Request error downloading PDF from {url}: {e}")
    except Exception as e:
        # Intentionally broad: PyMuPDF (fitz) raises arbitrary C-level
        # exceptions (RuntimeError, ValueError, etc.) for corrupt PDFs.
        logger.warning(f"PDF parsing failed for {url}: {e}")

    return None


def parse_local_pdf(path: Path) -> str | None:
    """Extract and clean text from a local PDF file using PyMuPDF (fitz).

    Args:
        path: Path to the PDF file.

    Returns:
        Cleaned text content if successful, None for empty/corrupt PDFs.

    Raises:
        FileNotFoundError: If the file does not exist.
    """
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")

    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.error("PyMuPDF not available — install with: pip install pymupdf")
        return None

    try:
        doc = fitz.open(str(path))
        return _extract_and_close_pdf(doc)

    except Exception as e:
        # Intentionally broad: PyMuPDF (fitz) raises arbitrary C-level
        # exceptions (RuntimeError, ValueError, etc.) for corrupt PDFs.
        logger.warning(f"PDF parsing failed for {path.name}: {e}")
        return None


def _extract_clean_pdf_text(doc: Any) -> str:
    """Internal: Extract and clean text from a PyMuPDF Document.

    Performs layout-aware cleaning:
    1. Removes headers and footers using heuristic margins.
    2. Truncates the document at the 'References' section to avoid LLM
       hallucinations from bibliography gene mentions.
    """
    text_parts: list[str] = []

    for page in doc:
        # PyMuPDF blocks contain coordinates, text, a block number, and type.
        blocks = page.get_text("blocks")

        page_text_parts = []
        for b in blocks:
            # Skip non-text blocks (type 0 is text)
            if b[6] != 0:
                continue

            y0, y1 = b[1], b[3]

            # Filter out headers and footers
            if y0 < _PDF_TOP_MARGIN or y1 > _PDF_BOTTOM_MARGIN:
                continue

            text = b[4].strip()
            if text:
                page_text_parts.append(text)

        if page_text_parts:
            text_parts.append("\n\n".join(page_text_parts))

    full_text = "\n\n".join(text_parts)

    # Truncate at the first common back-matter header in the latter half.
    match = _BACK_MATTER_PATTERN.search(full_text, len(full_text) // 2)
    return full_text[: match.start()] if match else full_text


async def fetch_abstract(pmid: str) -> str | None:
    """Fetch abstract for a given PMID from PubMed.

    Args:
        pmid: PubMed ID.

    Returns:
        Abstract text if available, None otherwise.
    """
    params = get_ncbi_params(
        {
            "db": "pubmed",
            "id": pmid,
            "rettype": "abstract",
            "retmode": "xml",
        }
    )

    try:
        client = await _client_manager.get()
        resp = await client.get(NCBI_EFETCH_URL, params=params)

        if resp.status_code != 200:
            logger.debug(f"Abstract fetch failed for PMID {pmid}: {resp.status_code}")
            return None

        return _parse_abstract_xml(resp.content)

    except httpx.TimeoutException:
        logger.warning(f"Timeout fetching abstract for PMID {pmid}")
    except httpx.RequestError as e:
        logger.warning(f"Request error fetching abstract for PMID {pmid}: {e}")
    except etree.XMLSyntaxError as e:
        logger.error(f"XML parsing failed for abstract PMID {pmid}: {e}")

    return None
