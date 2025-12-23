from dataclasses import dataclass
from typing import List, Optional

import httpx


@dataclass
class ValidationResult:
    is_valid: bool
    errors: List[str]
    warnings: List[str]
    normalized_data: Optional[dict]


CONFIDENCE_THRESHOLD = 0.7


async def validate_gene_entry(entry: dict) -> ValidationResult:
    """Multi-stage gene validation."""
    errors, warnings = [], []

    # Stage 1: Confidence threshold
    if entry.get("confidence", 0) < CONFIDENCE_THRESHOLD:
        errors.append(f"Low confidence: {entry['confidence']}")
        return ValidationResult(False, errors, warnings, None)

    # Stage 2: NCBI Gene validation
    gene_symbol = entry["gene_symbol"]
    ncbi_info = await verify_ncbi_gene(gene_symbol)

    if not ncbi_info:
        errors.append(f"Gene '{gene_symbol}' not found in NCBI Gene")
        return ValidationResult(False, errors, warnings, None)

    # Normalize gene symbol to official NCBI symbol
    if ncbi_info["symbol"] != gene_symbol:
        warnings.append(f"Normalized '{gene_symbol}' -> '{ncbi_info['symbol']}'")
        entry["gene_symbol"] = ncbi_info["symbol"]

    # Stage 3: GWAS trait validation (matches dashboard's actual GWAS traits)
    valid_traits = {
        "WMH",
        "SVS",
        "BG-PVS",
        "WM-PVS",
        "HIP-PVS",
        "PSMD",
        "extreme-cSVD",
        "FA",
        "lacunes",
        "stroke",
    }
    for trait in entry.get("gwas_trait", []):
        if trait not in valid_traits:
            warnings.append(f"Unknown GWAS trait: {trait}")

    # Stage 4: OMIM validation (if provided)
    if entry.get("omim_number"):
        omim_valid = await verify_omim_number(entry["omim_number"])
        if not omim_valid:
            warnings.append(f"OMIM {entry['omim_number']} not verified")

    return ValidationResult(True, errors, warnings, entry)


async def verify_ncbi_gene(symbol: str) -> Optional[dict]:
    """Query NCBI Gene database to verify gene symbol."""
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    params = {
        "db": "gene",
        "term": f"{symbol}[Gene Name] AND Homo sapiens[Organism]",
        "retmode": "json",
    }
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params=params)
        data = resp.json()
        if data["esearchresult"]["count"] != "0":
            gene_id = data["esearchresult"]["idlist"][0]
            return await fetch_gene_details(gene_id)
    return None


async def validate_trial_entry(entry: dict) -> ValidationResult:
    """Validate clinical trial entry against registries."""
    errors, warnings = [], []

    # Stage 1: Confidence threshold
    if entry.get("confidence", 0) < CONFIDENCE_THRESHOLD:
        errors.append(f"Low confidence: {entry['confidence']}")
        return ValidationResult(False, errors, warnings, None)

    # Stage 2: Registry ID validation (supports NCT, ISRCTN, ACTRN, ChiCTR)
    registry_id = entry.get("registry_ID", "")
    if registry_id:
        if registry_id.startswith("NCT"):
            valid = await verify_clinicaltrials_gov(registry_id)
        elif registry_id.startswith("ISRCTN"):
            valid = await verify_isrctn(registry_id)
        elif registry_id.startswith("ChiCTR"):
            valid = await verify_chictr(registry_id)
        elif registry_id.startswith("ACTRN"):
            valid = await verify_anzctr(registry_id)
        else:
            valid = True  # Accept other registries with warning
            warnings.append(f"Registry {registry_id} not auto-verified")

        if not valid:
            errors.append(f"Registry ID {registry_id} not found")
            return ValidationResult(False, errors, warnings, None)
    else:
        warnings.append("No registry ID provided")

    # Stage 3: Required fields (matches table2.csv schema)
    required = ["drug", "mechanism_of_action", "svd_population"]
    for field in required:
        if not entry.get(field):
            errors.append(f"Missing required field: {field}")

    if errors:
        return ValidationResult(False, errors, warnings, None)

    return ValidationResult(True, errors, warnings, entry)


async def verify_omim_number(omim_number: str) -> bool:
    """Verify OMIM number format and basic validity.

    OMIM numbers are 6-digit identifiers. Full API access requires registration,
    so we perform basic format validation and optionally check the OMIM website.
    """
    import re

    # Validate 6-digit format
    if not re.match(r"^\d{6}$", str(omim_number).strip()):
        return False

    # Attempt to verify via OMIM website (no API key required for basic check)
    url = f"https://omim.org/entry/{omim_number}"

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        try:
            resp = await client.head(url)
            # 200 = exists, 404 = not found
            return resp.status_code == 200
        except Exception:
            # If we can't reach OMIM, accept valid format
            return True


async def fetch_gene_details(gene_id: str) -> Optional[dict]:
    """Fetch gene details from NCBI using esummary.

    Returns gene symbol and other metadata for normalization.
    """
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
    params = {"db": "gene", "id": gene_id, "retmode": "json"}

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(url, params=params)
            if resp.status_code != 200:
                return None

            data = resp.json()
            result = data.get("result", {})
            gene_data = result.get(gene_id, {})

            if not gene_data or "error" in gene_data:
                return None

            return {
                "gene_id": gene_id,
                "symbol": gene_data.get("name", ""),
                "description": gene_data.get("description", ""),
                "chromosome": gene_data.get("chromosome", ""),
                "aliases": gene_data.get("otheraliases", "").split(", ")
                if gene_data.get("otheraliases")
                else [],
            }

        except Exception:
            return None


async def verify_clinicaltrials_gov(registry_id: str) -> bool:
    """Verify trial exists on ClinicalTrials.gov using their API.

    Uses the ClinicalTrials.gov API v2 to check if the NCT ID exists.
    """
    # Normalize NCT ID format
    nct_id = registry_id.strip().upper()
    if not nct_id.startswith("NCT"):
        return False

    url = f"https://clinicaltrials.gov/api/v2/studies/{nct_id}"

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(url)
            # 200 = exists, 404 = not found
            return resp.status_code == 200
        except Exception:
            # If API is unavailable, accept valid format
            return True


async def verify_isrctn(registry_id: str) -> bool:
    """Verify trial exists on ISRCTN registry.

    Checks the ISRCTN website for the trial page.
    """
    # Normalize ISRCTN ID
    isrctn_id = registry_id.strip().upper()
    if not isrctn_id.startswith("ISRCTN"):
        return False

    # ISRCTN website URL
    url = f"https://www.isrctn.com/{isrctn_id}"

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        try:
            resp = await client.head(url)
            # 200 = exists
            return resp.status_code == 200
        except Exception:
            # If unavailable, accept valid format
            return True


async def verify_chictr(registry_id: str) -> bool:
    """Verify trial exists on Chinese Clinical Trial Registry.

    ChiCTR has limited API access, so we verify format and check website.
    """
    import re

    # Normalize ChiCTR ID (format: ChiCTR followed by digits)
    chictr_id = registry_id.strip()
    if not re.match(r"^ChiCTR\d+$", chictr_id, re.IGNORECASE):
        return False

    # ChiCTR website URL (English version)
    url = f"https://www.chictr.org.cn/showprojen.html?proj={chictr_id}"

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        try:
            resp = await client.get(url)
            # Check if we got a valid page (not just redirect)
            if resp.status_code == 200:
                # Page exists, but might be empty - do basic content check
                content = resp.text.lower()
                return "not found" not in content and len(content) > 1000
            return False
        except Exception:
            # If unavailable, accept valid format
            return True


async def verify_anzctr(registry_id: str) -> bool:
    """Verify trial exists on Australian New Zealand Clinical Trials Registry.

    Uses the ANZCTR website to verify the trial exists.
    """
    import re

    # Normalize ACTRN ID (format: ACTRN followed by 14 digits)
    actrn_id = registry_id.strip().upper()
    if not re.match(r"^ACTRN\d{14}$", actrn_id):
        # Try without strict digit count
        if not actrn_id.startswith("ACTRN"):
            return False

    # ANZCTR website URL
    url = f"https://www.anzctr.org.au/Trial/Registration/TrialReview.aspx?id={actrn_id}"

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                # Check if it's a valid trial page vs error page
                content = resp.text.lower()
                return "trial not found" not in content and "error" not in content[:500]
            return False
        except Exception:
            # If unavailable, accept valid format
            return True
