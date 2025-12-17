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
    """Fetch full text from PubMed Central."""
    raise NotImplementedError(f"fetch_pmc_fulltext not implemented for {pmid}")


async def download_and_parse_pdf(url: str) -> Optional[str]:
    """Download PDF from URL and extract text."""
    raise NotImplementedError(f"download_and_parse_pdf not implemented for {url}")


async def fetch_abstract(pmid: str) -> Optional[str]:
    """Fetch abstract for a given PMID."""
    raise NotImplementedError(f"fetch_abstract not implemented for {pmid}")