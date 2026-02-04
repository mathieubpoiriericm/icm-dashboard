"""LLM-based gene extraction using Claude API.

Extracts genes with putative causal links to cSVD from research papers
using the Anthropic Claude API.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from typing import Any, Final

import anthropic
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

# --- Constants ---
MAX_PAPER_TEXT_CHARS: Final[int] = 50_000  # Claude context limit buffer for extraction
DEFAULT_MODEL: Final[str] = "claude-sonnet-4-20250514"
DEFAULT_MAX_TOKENS: Final[int] = 4096

# Rate limiting and retry configuration
MAX_RETRIES: Final[int] = 5
BASE_RETRY_DELAY: Final[float] = 60.0  # Start with 60 second delay on rate limit
MAX_RETRY_DELAY: Final[float] = 300.0  # Cap at 5 minutes
JITTER_FACTOR: Final[float] = 0.25  # Add up to 25% random jitter


class GeneEntry(BaseModel):
    """Extracted gene entry from paper analysis."""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_default=True,
    )

    gene_symbol: str
    protein_name: str | None = None
    gwas_trait: list[str] = Field(default_factory=list)
    mendelian_randomization: bool = False
    omics_evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    causal_evidence_summary: str | None = None


EXTRACTION_PROMPT: Final[
    str
] = """You are an expert in cerebral small vessel disease (cSVD) research and multi-omics studies (genomics, transcriptomics, proteomics, epigenomics).

TASK: Extract genes that are putatively causally linked to cSVD.

PRIMARY CRITERION (REQUIRED):
Include genes where the paper presents ANY evidence suggesting a putative causal relationship with cSVD or cSVD-related phenotypes:
- White matter hyperintensities (WMH)
- Lacune
- White matter lesion
- Lacunar stroke / lacunar infarcts
- Perivascular spaces (PVS, BG-PVS, WM-PVS)
- Small vessel stroke (SVS)
- Cerebral microbleeds
- PSMD (peak width of skeletonized mean diffusivity)
- Other cSVD imaging markers

Putative causal evidence includes: GWAS associations, fine-mapping, colocalization, Mendelian randomization, functional studies, expression QTLs, animal/cell models, or any mechanistic evidence linking the gene to cSVD pathology.

OPTIONAL SUPPORTING EVIDENCE (record if present, but NOT required):
- gwas_trait: Specific GWAS phenotype associations
- mendelian_randomization: Whether MR evidence supports causality
- omics_evidence: Results from TWAS, PWAS, EWAS, or other omics studies

For each gene, provide a brief causal_evidence_summary explaining WHY it is considered causally linked.

CONFIDENCE SCORING:
- 1.0: Direct causal evidence explicitly stated
- 0.7-0.9: Strong causal implication with supporting context
- 0.4-0.6: Mentioned in causal context but evidence is weak
- <0.4: Tangential mention, no clear causal implication

Return valid JSON with a "genes" array. If no causally-linked genes found, return {"genes": []}."""


# --- Async client singleton ---
_async_client: anthropic.AsyncAnthropic | None = None


def _get_async_client() -> anthropic.AsyncAnthropic:
    """Get or create shared async Anthropic client."""
    global _async_client
    if _async_client is None:
        _async_client = anthropic.AsyncAnthropic()
    return _async_client


async def extract_from_paper(text: str, pmid: str) -> list[dict[str, Any]]:
    """Extract genes using Claude API asynchronously with rate limit handling.

    Implements exponential backoff retry for rate limit errors. When a rate
    limit is hit, waits with increasing delays before retrying.

    Args:
        text: Full text content of the paper.
        pmid: PubMed ID for context.

    Returns:
        List of gene entries extracted from the paper.

    Raises:
        anthropic.RateLimitError: If API rate limit exceeded after all retries.
    """
    if not text or not text.strip():
        logger.warning(f"Empty text provided for PMID {pmid}")
        return []

    client = _get_async_client()
    last_error: anthropic.RateLimitError | None = None

    for attempt in range(MAX_RETRIES):
        try:
            response = await client.messages.create(
                model=DEFAULT_MODEL,
                max_tokens=DEFAULT_MAX_TOKENS,
                messages=[
                    {
                        "role": "user",
                        "content": f"{EXTRACTION_PROMPT}\n\n---\nPMID: {pmid}\n\n{text[:MAX_PAPER_TEXT_CHARS]}",
                    }
                ],
            )

            if not response.content:
                logger.warning(f"Empty response from Claude for PMID {pmid}")
                return []

            return parse_llm_response(response.content[0].text)

        except anthropic.RateLimitError as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                # Calculate delay with exponential backoff and jitter
                delay = min(BASE_RETRY_DELAY * (2**attempt), MAX_RETRY_DELAY)
                jitter = delay * JITTER_FACTOR * random.random()
                total_delay = delay + jitter

                logger.warning(
                    f"Rate limit hit for PMID {pmid} (attempt {attempt + 1}/{MAX_RETRIES}). "
                    f"Waiting {total_delay:.1f}s before retry..."
                )
                await asyncio.sleep(total_delay)
            else:
                logger.error(
                    f"Rate limit exceeded for PMID {pmid} after {MAX_RETRIES} attempts"
                )

        except anthropic.APIError as e:
            logger.error(f"Claude API error for PMID {pmid}: {e}")
            return []

    # All retries exhausted
    if last_error:
        raise last_error
    return []


def parse_llm_response(text: str) -> list[dict[str, Any]]:
    """Parse the LLM response JSON into gene entries.

    Handles various response formats:
    - Direct JSON object
    - JSON wrapped in markdown code blocks
    - Partial/malformed JSON with recovery

    Args:
        text: Raw text response from LLM.

    Returns:
        List of gene entries (empty list if parsing fails).
    """
    if not text or not text.strip():
        return []

    # 4-tier fallback strategy for parsing LLM JSON output
    # Needed because LLMs sometimes wrap JSON in markdown or include extra text

    # Tier 1: Extract JSON from markdown code blocks (```json ... ```)
    json_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if json_match:
        text = json_match.group(1)

    # Tier 2: Direct JSON parsing (cleanest case)
    try:
        parsed = json.loads(text.strip())
        if isinstance(parsed, dict):
            return parsed.get("genes", [])
    except json.JSONDecodeError:
        pass

    # Tier 3: Find JSON object anywhere in text (handles preamble/postamble)
    json_obj_match = re.search(r"\{[\s\S]*\}", text)
    if json_obj_match:
        try:
            parsed = json.loads(json_obj_match.group())
            if isinstance(parsed, dict):
                return parsed.get("genes", [])
        except json.JSONDecodeError:
            pass

    # Tier 4: Extract genes array directly (handles malformed outer object)
    genes_match = re.search(r'"genes"\s*:\s*(\[[\s\S]*?\])', text)
    if genes_match:
        try:
            return json.loads(genes_match.group(1))
        except json.JSONDecodeError:
            pass

    logger.warning("Failed to parse LLM response as JSON")
    return []
