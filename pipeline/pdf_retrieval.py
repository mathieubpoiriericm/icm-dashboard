import os
from typing import Optional
import httpx

UNPAYWALL_EMAIL = "mathieu.poirier3@mail.mcgill.ca"


async def get_fulltext(pmid: str, doi: Optional[str]) -> dict:
    """Attempt full-text retrieval, return text + source."""

    # Try PubMed Central first
    pmc_text = await fetch_pmc_fulltext(pmid)
    if pmc_text:
        return {"text": pmc_text, "source": "pmc", "fulltext": True}

    # Try Unpaywall for OA PDF
    if doi:
        oa_url = await check_unpaywall(doi)
        if oa_url:
            pdf_text = await download_and_parse_pdf(oa_url)
            if pdf_text:
                return {"text": pdf_text, "source": "unpaywall", "fulltext": True}

    # Fallback to abstract only
    abstract = await fetch_abstract(pmid)
    return {"text": abstract, "source": "abstract", "fulltext": False}


async def check_unpaywall(doi: str) -> Optional[str]:
    """Query Unpaywall API for open-access PDF URL."""
    url = f"https://api.unpaywall.org/v2/{doi}?email={UNPAYWALL_EMAIL}"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("is_oa") and data.get("best_oa_location"):
                return data["best_oa_location"].get("url_for_pdf")
    return None


async def fetch_pmc_fulltext(pmid: str) -> Optional[str]:
    """Fetch full text from PubMed Central.

    First checks if the PMID has a corresponding PMC article,
    then fetches the full text in XML format and extracts the body.
    """
    # Step 1: Convert PMID to PMCID
    convert_url = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"
    params = {"ids": pmid, "format": "json"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(convert_url, params=params)
            if resp.status_code != 200:
                return None

            data = resp.json()
            records = data.get("records", [])
            if not records or "pmcid" not in records[0]:
                return None  # No PMC article for this PMID

            pmcid = records[0]["pmcid"]

            # Step 2: Fetch full text from PMC
            pmc_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
            pmc_params = {"db": "pmc", "id": pmcid, "rettype": "xml"}
            api_key = os.getenv("NCBI_API_KEY")
            if api_key:
                pmc_params["api_key"] = api_key

            pmc_resp = await client.get(pmc_url, params=pmc_params)
            if pmc_resp.status_code != 200:
                return None

            # Parse XML and extract body text
            from lxml import etree  # type: ignore[import]

            root = etree.fromstring(pmc_resp.content)

            # Extract all paragraph text from the body
            paragraphs = root.findall(".//body//p")
            if not paragraphs:
                # Try alternate path for some PMC formats
                paragraphs = root.findall(".//sec//p")

            if not paragraphs:
                return None

            text_parts = []
            for p in paragraphs:
                # Get all text content including from child elements
                text = "".join(p.itertext())
                if text.strip():
                    text_parts.append(text.strip())

            return "\n\n".join(text_parts) if text_parts else None

        except Exception:
            return None


async def download_and_parse_pdf(url: str) -> Optional[str]:
    """Download PDF from URL and extract text using PyMuPDF (fitz).

    Downloads the PDF to memory and extracts text from all pages.
    Handles redirects and various PDF formats.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        # PyMuPDF not available, skip PDF extraction
        return None

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        try:
            resp = await client.get(url)
            if resp.status_code != 200:
                return None

            # Check content type
            content_type = resp.headers.get("content-type", "")
            if "pdf" not in content_type.lower() and not url.endswith(".pdf"):
                # Not a PDF, might be HTML landing page
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

        except Exception:
            return None


async def fetch_abstract(pmid: str) -> Optional[str]:
    """Fetch abstract for a given PMID from PubMed.

    Uses NCBI efetch API to retrieve the abstract text.
    Returns None if no abstract is available.
    """
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    params = {"db": "pubmed", "id": pmid, "rettype": "abstract", "retmode": "xml"}
    api_key = os.getenv("NCBI_API_KEY")
    if api_key:
        params["api_key"] = api_key

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(url, params=params)
            if resp.status_code != 200:
                return None

            # Parse XML to extract abstract
            from lxml import etree  # type: ignore[import]

            root = etree.fromstring(resp.content)

            # Find abstract text
            abstract_elem = root.find(".//AbstractText")
            if abstract_elem is not None:
                # Handle structured abstracts with labeled sections
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
                    # Single abstract text
                    return "".join(abstract_elem.itertext())

            return None

        except Exception:
            return None
