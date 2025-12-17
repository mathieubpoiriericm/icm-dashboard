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
    valid_traits = {"WMH", "SVS", "BG-PVS", "WM-PVS", "HIP-PVS",
                    "PSMD", "extreme-cSVD", "FA", "lacunes", "stroke"}
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
        "retmode": "json"
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
    """Verify OMIM number exists."""
    raise NotImplementedError(f"verify_omim_number not implemented for {omim_number}")


async def fetch_gene_details(gene_id: str) -> Optional[dict]:
    """Fetch gene details from NCBI."""
    raise NotImplementedError(f"fetch_gene_details not implemented for {gene_id}")


async def verify_clinicaltrials_gov(registry_id: str) -> bool:
    """Verify trial exists on ClinicalTrials.gov."""
    raise NotImplementedError(f"verify_clinicaltrials_gov not implemented for {registry_id}")


async def verify_isrctn(registry_id: str) -> bool:
    """Verify trial exists on ISRCTN registry."""
    raise NotImplementedError(f"verify_isrctn not implemented for {registry_id}")


async def verify_chictr(registry_id: str) -> bool:
    """Verify trial exists on Chinese Clinical Trial Registry."""
    raise NotImplementedError(f"verify_chictr not implemented for {registry_id}")


async def verify_anzctr(registry_id: str) -> bool:
    """Verify trial exists on ANZCTR registry."""
    raise NotImplementedError(f"verify_anzctr not implemented for {registry_id}")