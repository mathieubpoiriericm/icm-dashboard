import os
from typing import List, Set
from Bio import Entrez
from datetime import datetime, timedelta

Entrez.email = "mathieu.poirier@icm-institute.org"
Entrez.api_key = os.getenv("ENTREZ_KEY") or os.getenv("NCBI_API_KEY")

SVD_QUERY = """
("cerebral small vessel disease"[Title/Abstract] OR
 "white matter hyperintensities"[Title/Abstract] OR
 "lacunar stroke"[Title/Abstract] OR
 "CADASIL"[Title/Abstract]) AND
("gene"[tiab] OR "genetic"[tiab] OR
 "clinical trial"[tiab] OR "drug"[tiab])
"""

def search_recent_papers(days_back: int = 7) -> List[str]:
    """Return PMIDs of papers published in the last N days."""
    mindate = (datetime.now() - timedelta(days=days_back)).strftime("%Y/%m/%d")

    handle = Entrez.esearch(
        db="pubmed",
        term=SVD_QUERY,
        mindate=mindate,
        maxdate="3000",
        retmax=500,
        usehistory="y"
    )
    results = Entrez.read(handle)
    return results["IdList"]

def filter_new_pmids(pmids: List[str], existing: Set[str]) -> List[str]:
    """Remove PMIDs already in the dashboard."""
    return [p for p in pmids if p not in existing]