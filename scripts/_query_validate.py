"""PubMed query relevance validation — empirical MeSH + LLM hybrid scoring.

Sister module to ``scripts/_query_diagnose.py``. Where ``_query_diagnose``
treats ``SVD_QUERY`` as the gold standard and reports PMID overlap, this
module measures the **absolute cSVD-relevance** of each retrieved paper
using a two-tier scorer:

1. **NLM MeSH headings** (free, deterministic). A paper is relevant if
   any of its assigned MeSH terms intersects an empirically derived
   "cSVD-relevant MeSH set" built from the curated bibliography's own
   MeSH cache at ``data/bibentry/mesh/``. Population/indexing
   stopwords (Humans, Adult, Female...) are excluded.
2. **LLM fallback** (Claude Haiku 4.5 by default). For papers with no
   MeSH yet — typically recent / unindexed — Claude scores
   title + abstract against a binary relevance prompt. Per-PMID disk
   cache so re-runs are free.

Both the distilled query and ``SVD_QUERY`` are scored on the same
sample size and reported side-by-side. A second esearch run without
date restriction is used to compute a recall floor against the
bibliography PMIDs (sanity check: did the query retrieve known cSVD
papers?).

Network and LLM paths are injectable via the ``esearch_fetcher``,
``efetch_fetcher``, and ``llm_scorer`` arguments on ``run_validate`` so
the test suite runs fully offline.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import random
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

from lxml import etree  # type: ignore[import-untyped]

from scripts._query_diagnose import (
    PUBMED_ESEARCH_HARD_CAP,
    PaperMeta,
    _default_efetch_fetcher,
    esearch_pmids,
)
from scripts.distill_pubmed_keywords import (
    _MESH_STOP_TERMS_CASEFOLD,
    _SAFE_PARSER,
    _atomic_write_json,
    _coerce_to_bytes,
    _element_text,
)

logger = logging.getLogger(__name__)

_PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
DEFAULT_VALIDATE_CACHE_DIR: Final[Path] = (
    _PROJECT_ROOT / "data" / "bibentry" / "_validate_cache"
)
DEFAULT_MESH_BIBLIOGRAPHY_DIR: Final[Path] = (
    _PROJECT_ROOT / "data" / "bibentry" / "mesh"
)
DEFAULT_BIBLIOGRAPHY_XML_DIR: Final[Path] = _PROJECT_ROOT / "data" / "bibentry" / "xml"
DEFAULT_VALIDATE_OUTPUT_DIR: Final[Path] = _PROJECT_ROOT / "logs" / "query_validate"

DEFAULT_VALIDATE_SAMPLE: Final[int] = 200
DEFAULT_VALIDATE_MESH_THRESHOLD: Final[int] = 3
DEFAULT_VALIDATE_SEED: Final[int] = 0
DEFAULT_EFETCH_BATCH: Final[int] = 50

# Floor terms that always count as cSVD-relevant even if they don't make
# the empirical threshold (e.g. a small bibliography may not contain all
# of these as MeSH). Keeps the relevant set defensible across bibliography
# updates.
_RELEVANT_MESH_FLOOR: Final[frozenset[str]] = frozenset(
    {
        "Cerebral Small Vessel Diseases",
        "CADASIL",
        "Leukoaraiosis",
        "Cerebral Amyloid Angiopathy",
    }
)

# Default model for LLM relevance scoring. Haiku 4.5 is fast and cheap
# enough that a 200-paper validation run costs ~$0.10; for binary
# in/out-of-scope judgements its accuracy is comparable to Opus.
DEFAULT_LLM_MODEL: Final[str] = "claude-haiku-4-5-20251001"

# Prompt for the LLM relevance scorer. Cached on the system block so
# repeat calls within one validation run hit the cache.
_RELEVANCE_SYSTEM_PROMPT: Final[str] = """\
You are a biomedical literature relevance classifier. Your task is to \
decide whether a PubMed paper is primarily about cerebral small vessel \
disease (cSVD) — also written "SVD" or "small vessel disease of the \
brain".

A paper IS relevant if it is primarily about:
- Cerebral small vessel disease itself or its named subtypes (CADASIL, \
CARASIL, sporadic cSVD, cerebral amyloid angiopathy, Binswanger disease)
- Imaging or pathology hallmarks of cSVD: white matter \
hyperintensities, lacunar infarcts/lacunar stroke, cerebral \
microbleeds, enlarged perivascular spaces, cortical superficial \
siderosis, brain atrophy in a cSVD context
- Genetic, molecular, or mechanistic studies of cSVD pathophysiology
- Clinical aspects of cSVD: epidemiology, risk factors, vascular \
cognitive impairment, vascular dementia driven by cSVD, post-stroke \
cognitive decline due to cSVD

A paper is NOT relevant if it is primarily about:
- General stroke without a small-vessel focus (large-vessel ischemic \
stroke, cardioembolic stroke, hemorrhagic stroke not due to cSVD)
- Alzheimer's disease or other neurodegenerative dementias without a \
cSVD focus
- Other neurological diseases that don't primarily involve small \
cerebral vessels
- General cardiovascular disease, hypertension, atherosclerosis, or \
vascular biology that doesn't focus on brain small vessels
- General methodology / tooling papers that mention cSVD only in passing

Respond with a JSON object with exactly these three fields:
- "relevant": boolean — true if the paper is primarily about cSVD
- "confidence": float in [0.0, 1.0] — your confidence in the verdict
- "reason": short string (<= 200 chars) explaining the verdict
"""

_RELEVANCE_USER_TEMPLATE: Final[str] = """\
PMID: {pmid}
Title: {title}

Abstract:
{abstract}
"""

# JSON schema for structured output from the Anthropic API. Hand-written
# (rather than derived from a Pydantic model) so this module doesn't
# pull in the gene-extraction provider machinery.
_RELEVANCE_JSON_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "relevant": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "reason": {"type": "string", "maxLength": 400},
    },
    "required": ["relevant", "confidence", "reason"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# DATA CLASSES
# ---------------------------------------------------------------------------


RelevanceSource = Literal["mesh", "llm", "unscoreable"]


@dataclass(slots=True)
class PaperRecord:
    """Title + abstract + journal/year + MeSH for one PMID.

    Strict superset of ``PaperMeta`` from the diagnose module; we keep
    it separate so the two modules' caches stay independent and so we
    can extend this one with abstract text without growing the diagnose
    schema.
    """

    pmid: str
    title: str
    abstract: str
    journal: str
    year: str
    mesh: list[str]


@dataclass(slots=True)
class RelevanceScore:
    """One paper's relevance verdict and the evidence that produced it."""

    pmid: str
    relevant: bool
    source: RelevanceSource
    confidence: float
    reason: str
    matched_mesh: str | None = None


@dataclass(slots=True)
class RecallFloor:
    """Recall against a known cSVD PMID set (sanity check)."""

    retrieved: int
    total_gold: int
    missing: list[str]

    @property
    def recall(self) -> float:
        if self.total_gold == 0:
            return 0.0
        return self.retrieved / self.total_gold


@dataclass(slots=True)
class QueryValidation:
    """Per-query validation outcome."""

    label: str
    query: str
    total_pmids: int
    truncated: bool
    sample_pmids: list[str]
    scores: list[RelevanceScore]
    recall_floor: RecallFloor

    @property
    def scoreable_sample(self) -> int:
        return sum(1 for s in self.scores if s.source != "unscoreable")

    @property
    def relevant_count(self) -> int:
        return sum(1 for s in self.scores if s.relevant)

    @property
    def precision(self) -> float | None:
        if self.scoreable_sample == 0:
            return None
        return self.relevant_count / self.scoreable_sample

    def source_counts(self) -> dict[RelevanceSource, int]:
        counts: dict[RelevanceSource, int] = {
            "mesh": 0,
            "llm": 0,
            "unscoreable": 0,
        }
        for s in self.scores:
            counts[s.source] += 1
        return counts


@dataclass(slots=True)
class ValidationReport:
    """Side-by-side validation of distilled vs. production queries."""

    distilled: QueryValidation
    production: QueryValidation
    relevant_mesh_set: list[str]
    gold_pmids: list[str]
    sample_size: int
    seed: int
    validate_since: str | None
    validate_until: str | None
    llm_model: str
    timestamp: str = field(
        default_factory=lambda: _dt.datetime.now().isoformat(timespec="seconds")
    )


# ---------------------------------------------------------------------------
# MESH SET DERIVATION
# ---------------------------------------------------------------------------


def derive_relevant_mesh_set(
    mesh_dir: Path,
    *,
    min_papers: int = DEFAULT_VALIDATE_MESH_THRESHOLD,
    floor: Iterable[str] = _RELEVANT_MESH_FLOOR,
) -> set[str]:
    """Build a "cSVD-relevant" MeSH set from the cached bibliography MeSH.

    Loads every ``*.json`` file in ``mesh_dir`` (one per bibliography
    PMID, written by ``scripts.distill_pubmed_keywords.fetch_mesh_terms``).
    A MeSH descriptor counts as cSVD-relevant when it appears in
    ``>= min_papers`` bibliography papers, minus the population /
    indexing stopwords already filtered out elsewhere in the distill
    script. The ``floor`` set is unioned in so canonical cSVD terms
    remain even if the local bibliography doesn't include them.

    Raises ``FileNotFoundError`` if ``mesh_dir`` doesn't exist or is
    empty — the cache is populated by the distill script's MeSH harvest
    and must be present before validation can run.
    """
    if not mesh_dir.exists():
        raise FileNotFoundError(
            f"Bibliography MeSH cache not found at {mesh_dir}. Run the "
            "distill script once (without --no-mesh) to populate it."
        )

    counts: Counter[str] = Counter()
    papers_seen = 0
    for path in sorted(mesh_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(f"Skipping corrupt MeSH cache {path.name}: {exc}")
            continue
        descriptors = payload.get("descriptors") if isinstance(payload, dict) else None
        if not isinstance(descriptors, list):
            continue
        papers_seen += 1
        # Count each term at most once per paper so a single paper can't
        # push a term over the threshold on its own.
        seen_in_paper: set[str] = set()
        for desc in descriptors:
            if not isinstance(desc, dict):
                continue
            term = desc.get("term")
            if not isinstance(term, str) or not term:
                continue
            if term.casefold() in _MESH_STOP_TERMS_CASEFOLD:
                continue
            seen_in_paper.add(term)
        for term in seen_in_paper:
            counts[term] += 1

    if papers_seen == 0:
        raise FileNotFoundError(
            f"No usable MeSH cache files in {mesh_dir}. Re-run the "
            "distill script to repopulate the cache."
        )

    empirical = {term for term, count in counts.items() if count >= min_papers}
    return empirical | set(floor)


# ---------------------------------------------------------------------------
# PUBMED EFETCH (title + abstract + MeSH)
# ---------------------------------------------------------------------------


def parse_pubmed_xml_for_records(xml_bytes: bytes) -> dict[str, PaperRecord]:
    """Parse PubMed efetch XML into ``{pmid: PaperRecord}``.

    Like ``_query_diagnose.parse_pubmed_xml_for_meta`` but also pulls the
    ``<Abstract><AbstractText>`` segments so the LLM scorer has text to
    work with for unindexed papers. Multiple ``<AbstractText>`` segments
    (structured abstracts: Background/Methods/Results/Conclusions) are
    concatenated in document order with single spaces.

    Raises ``etree.XMLSyntaxError`` on malformed XML.
    """
    root = etree.fromstring(xml_bytes, parser=_SAFE_PARSER)
    out: dict[str, PaperRecord] = {}
    for article in root.findall(".//PubmedArticle"):
        pmid = _element_text(article.find(".//MedlineCitation/PMID"))
        if not pmid:
            continue
        title = _element_text(article.find(".//ArticleTitle"))
        abstract_parts = [
            _element_text(seg) for seg in article.findall(".//Abstract/AbstractText")
        ]
        abstract = " ".join(p for p in abstract_parts if p)
        journal_el = article.find(".//Journal/Title")
        if journal_el is None or not (journal_el.text or "").strip():
            journal_el = article.find(".//MedlineJournalInfo/MedlineTA")
        journal = _element_text(journal_el)
        year_el = article.find(".//PubDate/Year")
        if year_el is None or not (year_el.text or "").strip():
            year_el = article.find(".//PubDate/MedlineDate")
        year_text = _element_text(year_el)
        year = year_text[:4] if year_text else ""
        mesh: list[str] = []
        for d_el in article.findall(".//MeshHeadingList/MeshHeading/DescriptorName"):
            term = _element_text(d_el)
            if term:
                mesh.append(term)
        out[pmid] = PaperRecord(
            pmid=pmid,
            title=title,
            abstract=abstract,
            journal=journal,
            year=year,
            mesh=mesh,
        )
    return out


def efetch_papers_with_abstract(
    pmids: list[str],
    *,
    batch_size: int = DEFAULT_EFETCH_BATCH,
    cache_dir: Path | None = None,
    use_cache: bool = True,
    fetcher: Callable[[list[str]], bytes] | None = None,
) -> dict[str, PaperRecord]:
    """Fetch title/abstract/journal/year/MeSH for each PMID; cache per-PMID.

    Mirrors ``_query_diagnose.efetch_metadata`` but persists a richer
    record (including ``abstract``) and uses a separate cache directory
    so the diagnose and validate workflows don't accidentally share
    schemas.
    """
    if not pmids:
        return {}
    cache_dir = cache_dir or DEFAULT_VALIDATE_CACHE_DIR

    out: dict[str, PaperRecord] = {}
    missing: list[str] = []
    for pmid in pmids:
        cache_path = cache_dir / f"efetch_{pmid}.json"
        if use_cache and cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning(f"Corrupt efetch cache for PMID {pmid}: {exc}")
                cached = None
            if isinstance(cached, dict) and cached.get("pmid") == pmid:
                mesh_raw = cached.get("mesh", [])
                mesh_list = (
                    [str(m) for m in mesh_raw if m]
                    if isinstance(mesh_raw, list)
                    else []
                )
                out[pmid] = PaperRecord(
                    pmid=pmid,
                    title=str(cached.get("title", "")),
                    abstract=str(cached.get("abstract", "")),
                    journal=str(cached.get("journal", "")),
                    year=str(cached.get("year", "")),
                    mesh=mesh_list,
                )
                continue
        missing.append(pmid)

    if not missing:
        return out

    if fetcher is None:
        fetcher = _default_efetch_fetcher

    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning(f"Could not create efetch cache dir {cache_dir}: {exc}")

    for start in range(0, len(missing), batch_size):
        batch = missing[start : start + batch_size]
        try:
            raw = fetcher(batch)
        except Exception as exc:  # noqa: BLE001 — surface any fetch failure
            logger.warning(f"efetch batch {start}-{start + len(batch)} failed: {exc}")
            continue
        coerced = _coerce_to_bytes(raw, f"efetch batch {start}-{start + len(batch)}")
        if coerced is None:
            continue
        try:
            parsed = parse_pubmed_xml_for_records(coerced)
        except etree.XMLSyntaxError as exc:
            logger.warning(
                f"efetch batch {start}-{start + len(batch)} XML parse error: {exc}"
            )
            continue
        for pmid in batch:
            record = parsed.get(pmid)
            if record is None:
                continue
            out[pmid] = record
            try:
                _atomic_write_json(
                    cache_dir / f"efetch_{pmid}.json",
                    {
                        "pmid": pmid,
                        "title": record.title,
                        "abstract": record.abstract,
                        "journal": record.journal,
                        "year": record.year,
                        "mesh": record.mesh,
                    },
                )
            except OSError as exc:
                logger.warning(f"Could not write efetch cache for PMID {pmid}: {exc}")

    return out


# ---------------------------------------------------------------------------
# LLM RELEVANCE SCORER
# ---------------------------------------------------------------------------


# Public type alias — accepts an injectable callable so tests skip the
# Anthropic SDK entirely. Real production scorer is ``LlmRelevanceScorer``.
# ``None`` return signals "scorer unavailable" (missing API key, malformed
# response, etc.) and is surfaced to callers as ``source='unscoreable'``.
LlmScorerCallable = Callable[[PaperRecord], "LlmVerdict | None"]


@dataclass(slots=True)
class LlmVerdict:
    """Raw LLM judgement before it's combined into a ``RelevanceScore``."""

    relevant: bool
    confidence: float
    reason: str


class LlmRelevanceScorer:
    """Dedicated Claude client for binary cSVD-relevance scoring.

    Intentionally separate from ``pipeline/llm_providers/anthropic_provider.py``
    — the gene-extraction provider's adaptive thinking, structured gene
    schema, and long streaming setup are overkill for a single-shot
    binary classification. We use the synchronous client, the system
    prompt is cached, and per-PMID results are persisted to
    ``cache_dir`` so re-runs are free.

    Degrades to ``unscoreable`` (returns ``None`` from ``score``) when
    ``ANTHROPIC_API_KEY`` is missing rather than crashing — that lets
    ``--validate-no-llm-fallback`` and "API key isn't set" produce the
    same observable behavior.
    """

    def __init__(
        self,
        *,
        model: str = DEFAULT_LLM_MODEL,
        cache_dir: Path | None = None,
        use_cache: bool = True,
    ) -> None:
        self._model = model
        self._cache_dir = cache_dir or DEFAULT_VALIDATE_CACHE_DIR
        self._use_cache = use_cache
        self._client: Any = None
        self._client_disabled = False

    @property
    def model(self) -> str:
        return self._model

    def cache_path(self, pmid: str) -> Path:
        """Disk-cache path for ``pmid`` under the configured model."""
        safe_model = self._model.replace("/", "_")
        return self._cache_dir / f"llm_{pmid}_{safe_model}.json"

    def _get_client(self) -> Any | None:
        if self._client_disabled:
            return None
        if self._client is not None:
            return self._client
        if not os.getenv("ANTHROPIC_API_KEY"):
            logger.warning(
                "ANTHROPIC_API_KEY not set — LLM relevance fallback "
                "disabled; unindexed papers will be reported as "
                "'unscoreable'."
            )
            self._client_disabled = True
            return None
        try:
            import anthropic
        except ImportError as exc:
            logger.warning(
                f"anthropic SDK not importable ({exc}) — LLM relevance "
                "fallback disabled."
            )
            self._client_disabled = True
            return None
        self._client = anthropic.Anthropic()
        return self._client

    def score(self, record: PaperRecord) -> LlmVerdict | None:
        """Return an LLM verdict for one paper, or ``None`` if unavailable.

        ``None`` means "scorer is disabled or call failed"; callers
        surface that as ``source='unscoreable'``.
        """
        cache_path = self.cache_path(record.pmid)
        if self._use_cache and cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if isinstance(cached, dict) and "relevant" in cached:
                    return LlmVerdict(
                        relevant=bool(cached["relevant"]),
                        confidence=float(cached.get("confidence", 0.0)),
                        reason=str(cached.get("reason", "")),
                    )
            except (json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
                logger.warning(
                    f"Corrupt LLM cache for PMID {record.pmid}: {exc}; refetching"
                )

        client = self._get_client()
        if client is None:
            return None
        if not record.abstract and not record.title:
            logger.warning(
                f"PMID {record.pmid} has no title or abstract; cannot LLM-score."
            )
            return None

        try:
            response = client.messages.create(
                model=self._model,
                max_tokens=512,
                system=[
                    {
                        "type": "text",
                        "text": _RELEVANCE_SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[
                    {
                        "role": "user",
                        "content": _RELEVANCE_USER_TEMPLATE.format(
                            pmid=record.pmid,
                            title=record.title or "(no title)",
                            abstract=record.abstract or "(no abstract)",
                        ),
                    }
                ],
            )
        except Exception as exc:  # noqa: BLE001 — Anthropic SDK raises various
            logger.warning(f"LLM relevance call failed for PMID {record.pmid}: {exc}")
            return None

        verdict = _parse_llm_response(response)
        if verdict is None:
            return None

        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            _atomic_write_json(
                cache_path,
                {
                    "pmid": record.pmid,
                    "model": self._model,
                    "relevant": verdict.relevant,
                    "confidence": verdict.confidence,
                    "reason": verdict.reason,
                },
            )
        except OSError as exc:
            logger.warning(f"Could not write LLM cache for PMID {record.pmid}: {exc}")

        return verdict


def _parse_llm_response(response: Any) -> LlmVerdict | None:
    """Extract a structured ``LlmVerdict`` from the Anthropic message response.

    The Anthropic SDK returns ``message.content`` as a list of content
    blocks. We concatenate the text blocks and JSON-parse the result;
    malformed payloads are logged and treated as a non-verdict so the
    paper falls back to ``unscoreable``.
    """
    content = getattr(response, "content", None) or []
    text_parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            text_parts.append(text)
    payload_text = "".join(text_parts).strip()
    if not payload_text:
        logger.warning(
            "LLM response contained no text blocks; treating as unscoreable."
        )
        return None

    # Some models occasionally wrap JSON in ```json fences despite the schema
    # constraint; strip them defensively.
    if payload_text.startswith("```"):
        lines = payload_text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        payload_text = "\n".join(lines).strip()

    try:
        data = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        logger.warning(
            f"LLM response was not valid JSON: {exc}; payload={payload_text[:200]!r}"
        )
        return None

    if not isinstance(data, dict):
        logger.warning(f"LLM response was not a JSON object: {type(data).__name__}")
        return None

    try:
        return LlmVerdict(
            relevant=bool(data["relevant"]),
            confidence=max(0.0, min(1.0, float(data.get("confidence", 0.0)))),
            reason=str(data.get("reason", ""))[:400],
        )
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning(f"LLM response missing required fields: {exc}")
        return None


# ---------------------------------------------------------------------------
# RELEVANCE SCORING
# ---------------------------------------------------------------------------


def score_relevance(
    record: PaperRecord,
    relevant_mesh_set: set[str],
    *,
    llm_scorer: LlmScorerCallable | None = None,
) -> RelevanceScore:
    """Score one paper using MeSH-first, LLM-fallback strategy.

    - If ``record.mesh`` intersects ``relevant_mesh_set`` → relevant via
      MeSH (deterministic, ``confidence=1.0``).
    - If ``record.mesh`` is non-empty but no intersection → not relevant
      via MeSH ("indexed but no cSVD MeSH").
    - If ``record.mesh`` is empty → try ``llm_scorer`` (recent / unindexed
      paper). If the scorer returns ``None`` (no API key, no abstract,
      etc.) → ``source='unscoreable'``.
    """
    if record.mesh:
        matched = next(
            (term for term in record.mesh if term in relevant_mesh_set), None
        )
        if matched is not None:
            return RelevanceScore(
                pmid=record.pmid,
                relevant=True,
                source="mesh",
                confidence=1.0,
                reason=f"MeSH match: {matched}",
                matched_mesh=matched,
            )
        return RelevanceScore(
            pmid=record.pmid,
            relevant=False,
            source="mesh",
            confidence=1.0,
            reason="indexed but no cSVD-relevant MeSH heading",
        )

    if llm_scorer is None:
        return RelevanceScore(
            pmid=record.pmid,
            relevant=False,
            source="unscoreable",
            confidence=0.0,
            reason="no MeSH yet and LLM fallback disabled",
        )

    verdict = llm_scorer(record)
    if verdict is None:
        return RelevanceScore(
            pmid=record.pmid,
            relevant=False,
            source="unscoreable",
            confidence=0.0,
            reason="LLM scorer unavailable (no API key or call failed)",
        )
    return RelevanceScore(
        pmid=record.pmid,
        relevant=verdict.relevant,
        source="llm",
        confidence=verdict.confidence,
        reason=verdict.reason,
    )


# ---------------------------------------------------------------------------
# RECALL FLOOR
# ---------------------------------------------------------------------------


def compute_recall_floor(
    query_pmids: Iterable[str], gold_pmids: Iterable[str]
) -> RecallFloor:
    """Recall of ``gold_pmids`` against the retrieved set.

    Order-insensitive; duplicates collapsed.
    """
    retrieved_set = {str(p) for p in query_pmids}
    gold_set = {str(p) for p in gold_pmids}
    if not gold_set:
        return RecallFloor(retrieved=0, total_gold=0, missing=[])
    missing = sorted(gold_set - retrieved_set)
    found = gold_set & retrieved_set
    return RecallFloor(
        retrieved=len(found),
        total_gold=len(gold_set),
        missing=missing,
    )


def load_bibliography_gold_pmids(
    bibliography_xml_dir: Path = DEFAULT_BIBLIOGRAPHY_XML_DIR,
) -> list[str]:
    """Use the MODS XML filenames (PMID stems) as the gold-set PMIDs.

    The bibliography is the user's curated definition of cSVD-relevant
    literature; each file is named ``<pmid>.xml``. Non-numeric stems
    are skipped defensively.
    """
    if not bibliography_xml_dir.exists():
        return []
    pmids: list[str] = []
    for path in sorted(bibliography_xml_dir.glob("*.xml")):
        stem = path.stem
        if stem.isdigit():
            pmids.append(stem)
    return pmids


# ---------------------------------------------------------------------------
# SAMPLING
# ---------------------------------------------------------------------------


def sample_pmids(pmids: list[str], *, sample_size: int, seed: int) -> list[str]:
    """Deterministic random sample of ``pmids``.

    Returns the full list (sorted for determinism) when fewer PMIDs are
    available than ``sample_size``. ``seed`` is threaded through a
    fresh ``random.Random`` so the global RNG state is untouched.
    """
    if not pmids:
        return []
    if len(pmids) <= sample_size:
        return sorted(pmids)
    rng = random.Random(seed)
    return sorted(rng.sample(pmids, sample_size))


# ---------------------------------------------------------------------------
# REPORT RENDERING
# ---------------------------------------------------------------------------


def _format_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.1f}%"


def _format_precision_row(qv: QueryValidation) -> str:
    if qv.precision is None:
        return f"| {qv.label} | {qv.scoreable_sample}/{len(qv.scores)} | n/a |"
    return (
        f"| {qv.label} | {qv.relevant_count}/{qv.scoreable_sample} | "
        f"{_format_pct(qv.precision)} |"
    )


def _format_sample_paper_row(score: RelevanceScore) -> str:
    verdict = "✓" if score.relevant else "✗"
    reason = score.reason.replace("|", "\\|")[:140]
    return (
        f"| {score.pmid} | {verdict} | {score.source} | "
        f"{score.confidence:.2f} | {reason} |"
    )


def render_validate_report(report: ValidationReport) -> str:
    """Render the side-by-side Markdown validation report."""
    lines: list[str] = []
    lines.append(f"# PubMed query relevance validation — {report.timestamp}\n\n")

    window_a = report.validate_since or "(any)"
    window_b = report.validate_until or "(any)"
    lines.append(
        f"**Sampling window (pdat):** {window_a} → {window_b}. "
        f"**Sample size:** {report.sample_size} PMIDs per query "
        f"(seed={report.seed}). **LLM fallback:** {report.llm_model}.\n\n"
    )

    lines.append("## Queries\n\n")
    lines.append(f"### A — {report.distilled.label}\n\n")
    lines.append("```\n" + report.distilled.query + "\n```\n\n")
    lines.append(f"### B — {report.production.label}\n\n")
    lines.append("```\n" + report.production.query + "\n```\n\n")

    lines.append("## Precision\n\n")
    lines.append("| Query | Relevant / Scoreable | Precision |\n")
    lines.append("|---|---|---|\n")
    lines.append(_format_precision_row(report.distilled) + "\n")
    lines.append(_format_precision_row(report.production) + "\n\n")

    lines.append("## Recall floor (bibliography PMIDs retrieved)\n\n")
    lines.append("| Query | Retrieved / Gold | Recall | Missing |\n")
    lines.append("|---|---|---|---|\n")
    for qv in (report.distilled, report.production):
        rf = qv.recall_floor
        missing_preview = ", ".join(rf.missing[:5])
        if len(rf.missing) > 5:
            missing_preview += f", … (+{len(rf.missing) - 5} more)"
        if not missing_preview:
            missing_preview = "_(none)_"
        lines.append(
            f"| {qv.label} | {rf.retrieved}/{rf.total_gold} | "
            f"{_format_pct(rf.recall)} | {missing_preview} |\n"
        )
    lines.append("\n")

    lines.append("## Score sources (where each verdict came from)\n\n")
    lines.append("| Query | MeSH-scored | LLM-scored | Unscoreable | Total |\n")
    lines.append("|---|---|---|---|---|\n")
    for qv in (report.distilled, report.production):
        c = qv.source_counts()
        lines.append(
            f"| {qv.label} | {c['mesh']} | {c['llm']} | {c['unscoreable']} | "
            f"{len(qv.scores)} |\n"
        )
    lines.append("\n")

    lines.append("## Retrieved totals\n\n")
    lines.append("| Query | Total PMIDs (in window) | Truncated? |\n")
    lines.append("|---|---|---|\n")
    for qv in (report.distilled, report.production):
        flag = "⚠️ retmax cap hit" if qv.truncated else "no"
        lines.append(f"| {qv.label} | {qv.total_pmids} | {flag} |\n")
    lines.append("\n")

    lines.append(
        f"## Relevant-MeSH set used ({len(report.relevant_mesh_set)} terms)\n\n"
    )
    lines.append(
        "Empirically derived from the bibliography MeSH cache "
        "(`data/bibentry/mesh/`), with population stopwords removed "
        "and a small canonical floor unioned in.\n\n"
    )
    for term in sorted(report.relevant_mesh_set):
        lines.append(f"- {term}\n")
    lines.append("\n")

    for qv in (report.distilled, report.production):
        lines.append(f"## Sample papers — {qv.label}\n\n")
        if not qv.scores:
            lines.append("_(no sample papers — query returned 0 results)_\n\n")
            continue
        lines.append(
            "| PMID | Relevant? | Source | Confidence | Reason |\n"
            "|---|---|---|---|---|\n"
        )
        for s in qv.scores:
            lines.append(_format_sample_paper_row(s) + "\n")
        lines.append("\n")

    lines.append(_validate_interpretation_guide(report))

    return "".join(lines)


def _validate_interpretation_guide(report: ValidationReport) -> str:
    d = report.distilled
    p = report.production
    notes: list[str] = []
    if d.precision is None or p.precision is None:
        notes.append(
            "- One or both queries had no scoreable sample (no MeSH and "
            "no LLM verdict). Precision is undefined for those rows."
        )
    elif d.precision >= p.precision:
        notes.append(
            f"- The distilled query is **at least as precise as** "
            f"`{p.label}` on this sample "
            f"({_format_pct(d.precision)} vs {_format_pct(p.precision)})."
        )
    else:
        notes.append(
            f"- The distilled query is **less precise** than `{p.label}` "
            f"on this sample "
            f"({_format_pct(d.precision)} vs {_format_pct(p.precision)}). "
            "Consider narrowing the Title/Abstract clause, enabling "
            "`--dedupe-substrings`, or tightening the anchor phrase."
        )
    if d.recall_floor.total_gold > 0:
        if d.recall_floor.recall < 1.0:
            notes.append(
                f"- The distilled query misses "
                f"{d.recall_floor.total_gold - d.recall_floor.retrieved} "
                f"of the {d.recall_floor.total_gold} bibliography papers. "
                "Inspect the Missing column above; the distilled query "
                "should retrieve its own seed corpus when run without "
                "date restriction."
            )
        else:
            notes.append(
                "- The distilled query retrieves every bibliography "
                "paper — recall-floor sanity check passes."
            )
    if d.scoreable_sample < len(d.scores) * 0.5:
        notes.append(
            "- More than half of the distilled-query sample was "
            "unscoreable. Either set `ANTHROPIC_API_KEY` or pick a "
            "validation window with better MeSH coverage "
            "(e.g. `--validate-since` two or more years ago)."
        )
    body = "\n".join(notes) if notes else "- (no automated notes)"
    return "\n## Interpretation\n\n" + body + "\n"


def emit_validate_json(report: ValidationReport, path: Path) -> None:
    """Write the JSON sidecar with raw per-PMID scores + parameters."""
    payload: dict[str, Any] = {
        "timestamp": report.timestamp,
        "sample_size": report.sample_size,
        "seed": report.seed,
        "validate_since": report.validate_since,
        "validate_until": report.validate_until,
        "llm_model": report.llm_model,
        "relevant_mesh_set": sorted(report.relevant_mesh_set),
        "gold_pmids": list(report.gold_pmids),
        "queries": {
            "distilled": _query_validation_to_dict(report.distilled),
            "production": _query_validation_to_dict(report.production),
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(path, payload)


def _query_validation_to_dict(qv: QueryValidation) -> dict[str, Any]:
    return {
        "label": qv.label,
        "query": qv.query,
        "total_pmids": qv.total_pmids,
        "truncated": qv.truncated,
        "sample_pmids": list(qv.sample_pmids),
        "precision": qv.precision,
        "relevant_count": qv.relevant_count,
        "scoreable_sample": qv.scoreable_sample,
        "source_counts": qv.source_counts(),
        "recall_floor": {
            "retrieved": qv.recall_floor.retrieved,
            "total_gold": qv.recall_floor.total_gold,
            "recall": qv.recall_floor.recall,
            "missing": list(qv.recall_floor.missing),
        },
        "scores": [
            {
                "pmid": s.pmid,
                "relevant": s.relevant,
                "source": s.source,
                "confidence": s.confidence,
                "reason": s.reason,
                "matched_mesh": s.matched_mesh,
            }
            for s in qv.scores
        ],
    }


# ---------------------------------------------------------------------------
# END-TO-END DRIVER
# ---------------------------------------------------------------------------


def _validate_one_query(
    *,
    label: str,
    query: str,
    sample_size: int,
    seed: int,
    since: str | None,
    until: str | None,
    relevant_mesh_set: set[str],
    gold_pmids: list[str],
    llm_scorer: LlmScorerCallable | None,
    cache_dir: Path,
    esearch_fetcher: Callable[..., Any] | None,
    efetch_fetcher: Callable[[list[str]], bytes] | None,
) -> QueryValidation:
    """Run the validation pipeline for a single query."""
    in_window = esearch_pmids(
        query,
        retmax=PUBMED_ESEARCH_HARD_CAP,
        mindate=since,
        maxdate=until,
        cache_dir=cache_dir,
        fetcher=esearch_fetcher,
    )

    # Separate esearch without a date restriction, just for recall-floor
    # math against the bibliography. The bibliography papers are mostly
    # older than any recent sampling window, so we can't compute recall
    # against them from the windowed result.
    all_time = esearch_pmids(
        query,
        retmax=PUBMED_ESEARCH_HARD_CAP,
        mindate=None,
        maxdate=None,
        cache_dir=cache_dir,
        fetcher=esearch_fetcher,
    )

    sampled = sample_pmids(in_window.pmids, sample_size=sample_size, seed=seed)
    records = efetch_papers_with_abstract(
        sampled,
        cache_dir=cache_dir,
        fetcher=efetch_fetcher,
    )

    scores: list[RelevanceScore] = []
    for pmid in sampled:
        record = records.get(pmid)
        if record is None:
            scores.append(
                RelevanceScore(
                    pmid=pmid,
                    relevant=False,
                    source="unscoreable",
                    confidence=0.0,
                    reason="efetch returned no metadata",
                )
            )
            continue
        scores.append(score_relevance(record, relevant_mesh_set, llm_scorer=llm_scorer))

    return QueryValidation(
        label=label,
        query=query,
        total_pmids=in_window.total_count,
        truncated=in_window.truncated,
        sample_pmids=sampled,
        scores=scores,
        recall_floor=compute_recall_floor(all_time.pmids, gold_pmids),
    )


def run_validate(
    *,
    distilled_query: str,
    production_query: str,
    distilled_label: str = "distilled",
    production_label: str = "SVD_QUERY",
    sample_size: int = DEFAULT_VALIDATE_SAMPLE,
    seed: int = DEFAULT_VALIDATE_SEED,
    validate_since: str | None = None,
    validate_until: str | None = None,
    mesh_threshold: int = DEFAULT_VALIDATE_MESH_THRESHOLD,
    mesh_dir: Path = DEFAULT_MESH_BIBLIOGRAPHY_DIR,
    bibliography_xml_dir: Path = DEFAULT_BIBLIOGRAPHY_XML_DIR,
    llm_model: str = DEFAULT_LLM_MODEL,
    use_llm_fallback: bool = True,
    cache_dir: Path | None = None,
    esearch_fetcher: Callable[..., Any] | None = None,
    efetch_fetcher: Callable[[list[str]], bytes] | None = None,
    llm_scorer: LlmScorerCallable | None = None,
) -> ValidationReport:
    """End-to-end validation driver: esearch → sample → score → report.

    Network and LLM paths are all injectable for tests.
    ``llm_scorer=None`` and ``use_llm_fallback=True`` constructs a real
    ``LlmRelevanceScorer`` lazily; pass an explicit callable to bypass
    the Anthropic SDK entirely.
    """
    cache_dir = cache_dir or DEFAULT_VALIDATE_CACHE_DIR

    relevant_mesh_set = derive_relevant_mesh_set(mesh_dir, min_papers=mesh_threshold)
    gold_pmids = load_bibliography_gold_pmids(bibliography_xml_dir)

    effective_scorer: LlmScorerCallable | None = llm_scorer
    if effective_scorer is None and use_llm_fallback:
        scorer = LlmRelevanceScorer(model=llm_model, cache_dir=cache_dir)
        effective_scorer = scorer.score

    distilled = _validate_one_query(
        label=distilled_label,
        query=distilled_query,
        sample_size=sample_size,
        seed=seed,
        since=validate_since,
        until=validate_until,
        relevant_mesh_set=relevant_mesh_set,
        gold_pmids=gold_pmids,
        llm_scorer=effective_scorer,
        cache_dir=cache_dir,
        esearch_fetcher=esearch_fetcher,
        efetch_fetcher=efetch_fetcher,
    )
    production = _validate_one_query(
        label=production_label,
        query=production_query,
        sample_size=sample_size,
        seed=seed,
        since=validate_since,
        until=validate_until,
        relevant_mesh_set=relevant_mesh_set,
        gold_pmids=gold_pmids,
        llm_scorer=effective_scorer,
        cache_dir=cache_dir,
        esearch_fetcher=esearch_fetcher,
        efetch_fetcher=efetch_fetcher,
    )

    return ValidationReport(
        distilled=distilled,
        production=production,
        relevant_mesh_set=sorted(relevant_mesh_set),
        gold_pmids=gold_pmids,
        sample_size=sample_size,
        seed=seed,
        validate_since=validate_since,
        validate_until=validate_until,
        llm_model=llm_model,
    )


# Re-export for convenience so callers don't need to know that
# ``PaperMeta`` lives in the diagnose module.
__all__ = [
    "DEFAULT_LLM_MODEL",
    "DEFAULT_VALIDATE_CACHE_DIR",
    "DEFAULT_VALIDATE_MESH_THRESHOLD",
    "DEFAULT_VALIDATE_OUTPUT_DIR",
    "DEFAULT_VALIDATE_SAMPLE",
    "DEFAULT_VALIDATE_SEED",
    "LlmRelevanceScorer",
    "LlmScorerCallable",
    "LlmVerdict",
    "PaperMeta",
    "PaperRecord",
    "QueryValidation",
    "RecallFloor",
    "RelevanceScore",
    "RelevanceSource",
    "ValidationReport",
    "compute_recall_floor",
    "derive_relevant_mesh_set",
    "efetch_papers_with_abstract",
    "emit_validate_json",
    "load_bibliography_gold_pmids",
    "parse_pubmed_xml_for_records",
    "render_validate_report",
    "run_validate",
    "sample_pmids",
    "score_relevance",
]
