import os
from typing import List, Set
from Bio import Entrez
from datetime import datetime, timedelta

Entrez.email = "mathieu.poirier@icm-institute.org"  # type: ignore[assignment]
Entrez.api_key = os.getenv("ENTREZ_KEY") or os.getenv("NCBI_API_KEY")  # type: ignore[assignment]

# Primary disease terms for cSVD/SVD
DISEASE_TERMS = [
    "cerebral small vessel disease",
    "small vessel disease",
]

# cSVD markers/phenotypes (used to capture relevant papers)
MARKER_TERMS = [
    "stroke",
    "dementia",
    "lacunes",
    "white matter hyperintensities",
    "perivascular spaces",
    "cerebral microbleeds",
]

# General terms to capture genetic research
GENETIC_TERMS = [
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
]

def _build_query() -> str:
    """Build the PubMed query for cSVD/SVD genetic research."""
    # Papers explicitly about cSVD/SVD with genetic focus
    disease_clause = " OR ".join(f'"{t}"[Title/Abstract]' for t in DISEASE_TERMS)
    research_clause = " OR ".join(
        f'"{t}"[Title/Abstract]' for t in GENETIC_TERMS
    )
    main_query = f"(({disease_clause}) AND ({research_clause}))"

    # Papers about cSVD markers that mention cSVD/SVD context
    marker_clause = " OR ".join(f'"{t}"[Title/Abstract]' for t in MARKER_TERMS)
    marker_query = f"(({marker_clause}) AND ({disease_clause}))"

    return f"{main_query} OR {marker_query}"


SVD_QUERY = _build_query()


def search_recent_papers(days_back: int = 7) -> List[str]:
    """Return PMIDs of papers published in the last N days."""
    mindate = (datetime.now() - timedelta(days=days_back)).strftime("%Y/%m/%d")

    handle = Entrez.esearch(
        db="pubmed",
        term=SVD_QUERY,
        mindate=mindate,
        maxdate="3000",
        retmax=500,
        usehistory="y",
    )
    results = Entrez.read(handle)
    return results["IdList"]


def filter_new_pmids(pmids: List[str], existing: Set[str]) -> List[str]:
    """Remove PMIDs already in the dashboard."""
    return [p for p in pmids if p not in existing]
