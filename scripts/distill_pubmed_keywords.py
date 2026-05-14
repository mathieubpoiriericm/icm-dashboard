"""Distill PubMed-API-ready keywords from a curated MODS bibliography.

Reads MODS XML files in the input directory (default
``data/bibentry/xml/``), tokenises titles + abstracts (and, when
available, PMC full-text body sections), then ranks unigrams, bigrams,
trigrams and ALL-CAPS acronyms by **Dunning's log-likelihood ratio** of
foreground vs. a one-time PubMed baseline snapshot. LLR surfaces terms
that are *distinctive* to the corpus rather than merely frequent —
which is what makes them useful as PubMed search keywords.

A light rule-based lemmatiser collapses singular/plural variants for
aggregation while still displaying the most common surface form, so
the ranked output stays readable.

Optionally harvests **MeSH headings** (PubMed's curated medical
thesaurus) via NCBI E-utilities for each seed PMID, aggregating across
the corpus with major topics weighted 2×. The emitted Boolean query
combines MeSH and Title/Abstract clauses:

    (mesh1)[MeSH Terms] OR ... AND ("phrase1"[Title/Abstract] OR ...)

**PMC full-text enrichment** (on by default, ``--no-fulltext`` to
disable). For each seed PMID we resolve a PMCID via ``Entrez.elink``,
fetch the JATS XML via ``Entrez.efetch(db="pmc")``, and extract the
Introduction / Methods / Results / Discussion sections by ``sec-type``
attribute (with ``<title>``-text synonyms as backup). The result is
cached per-PMID under ``data/bibentry/fulltext/``; subsequent runs are
offline for cached PMIDs. Papers without a PMC version contribute
title+abstract only.

*Methodological note*: the cached PubMed baseline still reflects
title+abstract only of ~10k random PubMed records, so foreground
papers now contribute strictly more tokens than baseline papers do.
Rankings shift toward methods/results vocabulary that's
underrepresented in abstracts (procedural verbs, imaging-modality
names, assay terms). This is intentional for keyword distillation but
makes LLR scores no longer apples-to-apples with the pre-fulltext
version of this script.

The baseline cache must be built once before first ranking run; runs
are offline thereafter and the cache should be refreshed roughly
yearly:

    python scripts/distill_pubmed_keywords.py --build-baseline

Usage:
    python scripts/distill_pubmed_keywords.py --build-baseline
    python scripts/distill_pubmed_keywords.py
    python scripts/distill_pubmed_keywords.py --xml-dir data/bibentry/xml
    python scripts/distill_pubmed_keywords.py --no-mesh
    python scripts/distill_pubmed_keywords.py --no-fulltext
    python scripts/distill_pubmed_keywords.py --json --output keywords.json
    python scripts/distill_pubmed_keywords.py --query-format structured
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import gzip
import json
import logging
import math
import os
import re
import sys
import time
import warnings
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, TextIO

from lxml import etree  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CONSTANTS
# ---------------------------------------------------------------------------

# Resolve the default corpus path relative to the script so the CLI works
# regardless of the caller's working directory.
_PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
DEFAULT_XML_DIR: Final[Path] = _PROJECT_ROOT / "data" / "bibentry" / "xml"
DEFAULT_BASELINE_CACHE: Final[Path] = (
    _PROJECT_ROOT / "data" / "bibentry" / "baseline" / "pubmed_baseline.json.gz"
)
DEFAULT_MESH_CACHE_DIR: Final[Path] = _PROJECT_ROOT / "data" / "bibentry" / "mesh"
DEFAULT_FULLTEXT_CACHE_DIR: Final[Path] = (
    _PROJECT_ROOT / "data" / "bibentry" / "fulltext"
)

DEFAULT_TOP_N: Final[int] = 30
DEFAULT_MIN_DF: Final[int] = 2
DEFAULT_PHRASE_TOP: Final[int] = 10
DEFAULT_MESH_TOP: Final[int] = 10

DEFAULT_BASELINE_SIZE: Final[int] = 10_000
DEFAULT_BASELINE_BATCH: Final[int] = 200
DEFAULT_MESH_BATCH: Final[int] = 50
BASELINE_STALE_DAYS: Final[int] = 365
BASELINE_SCHEMA_VERSION: Final[int] = 1
FULLTEXT_SCHEMA_VERSION: Final[int] = 1
# Drop n-grams with baseline count < this from the cache file to keep
# it under ~50MB. Side effect: LLR for cSVD n-grams that happen to occur
# exactly once in baseline treats their baseline count as 0 (smoothed
# to 0.5), slightly over-estimating distinctiveness for those terms.
# Unigrams and acronyms are stored without filtering.
BASELINE_NGRAM_MIN_COUNT: Final[int] = 2

DEFAULT_MIN_LLR: Final[float] = 6.63  # chi-square p<0.01 at df=1

# Query-format choices — `_QUERY_FORMATS[0]` is "all" (default).
_QUERY_FORMATS: Final[tuple[str, ...]] = (
    "all", "structured", "mesh", "titleabstract",
)
_QUERY_FORMAT_VARIANTS: Final[tuple[str, ...]] = _QUERY_FORMATS[1:]

MIN_TOKEN_LENGTH: Final[int] = 3
MIN_ACRONYM_LENGTH: Final[int] = 2
# Upper bound covers known cSVD-relevant acronyms (CADASIL, CARASIL = 7;
# CAMRQ4 etc. = 6); was 6 in v1 and silently dropped CADASIL.
MAX_ACRONYM_LENGTH: Final[int] = 8

# NCBI rate limits — 3 req/s without API key, 10 req/s with.
_NCBI_SLEEP_NO_KEY: Final[float] = 0.34
_NCBI_SLEEP_WITH_KEY: Final[float] = 0.11
_NCBI_RETRY_BACKOFF: Final[tuple[float, ...]] = (1.0, 2.0, 4.0)

# Letter-led tokens that may carry intra-word hyphens. Hyphens must be
# followed by alphanumerics, which keeps "follow-up" intact but stops
# stray trailing hyphens leaking through.
_TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*"
)
_NUMERIC_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[\d\-]+$")
_PDAT_RANGE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^\d{4}:\d{4}$")

# Namespace wildcard — MODS files declare xmlns="http://www.loc.gov/mods/v3"
# but {*} keeps the queries robust if a record is namespace-stripped.
_NS: Final[str] = "{*}"

# Same hardening as pipeline.config.SAFE_XML_PARSER; inlined so the
# script stays runnable without putting the pipeline package on sys.path.
_SAFE_PARSER: Final[etree.XMLParser] = etree.XMLParser(
    resolve_entities=False, no_network=True
)

# Trimmed stopword set. The large biomedical-filler block from the v1
# script (study/patients/results/significant/...) is gone — LLR demotes
# those automatically against the PubMed baseline. What remains are
# grammatical tokens (articles/prepositions/pronouns/aux verbs) plus
# structured-abstract section labels that carry no information.
_STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        # Articles, prepositions, conjunctions
        "a", "an", "the", "and", "or", "but", "if", "of", "at", "by",
        "for", "with", "about", "against", "between", "into", "through",
        "during", "before", "after", "above", "below", "to", "from", "up",
        "down", "in", "out", "on", "off", "over", "under", "than", "then",
        "once", "here", "there", "when", "where", "why", "how", "as",
        "because", "while", "although", "though", "since", "unless",
        "until", "whether", "via", "across", "within", "without", "among",
        "per", "upon",
        # Pronouns / demonstratives
        "me", "my", "we", "our", "us", "you", "your", "yours", "he", "him",
        "his", "she", "her", "hers", "it", "its", "they", "them", "their",
        "theirs", "what", "which", "who", "whom", "this", "that", "these",
        "those",
        # Aux / common verbs
        "am", "is", "are", "was", "were", "be", "been", "being", "have",
        "has", "had", "having", "do", "does", "did", "doing", "would",
        "could", "should", "ought", "may", "might", "must", "can", "will",
        "shall", "let",
        # Structured-abstract section labels (appear as bare prefixes;
        # LLR helps but they still pollute trigrams).
        "background", "purpose", "objective", "objectives", "aim", "aims",
        "introduction", "discussion", "interpretation", "design", "setting",
        "interventions", "measurements", "outcomes", "outcome", "context",
        "methods", "results", "conclusions", "conclusion",
        # Number words
        "one", "two", "three", "four", "five", "six", "seven", "eight",
        "nine", "ten", "first", "second", "third", "fourth", "fifth",
    }
)

# Biomedical irregular plurals — looked up first by stem_key.
_IRREGULAR_PLURALS: Final[dict[str, str]] = {
    "analyses": "analysis",
    "diagnoses": "diagnosis",
    "prognoses": "prognosis",
    "syntheses": "synthesis",
    "hypotheses": "hypothesis",
    "criteria": "criterion",
    "phenomena": "phenomenon",
    "bases": "basis",
    "data": "datum",
    "loci": "locus",
    "nuclei": "nucleus",
    "mitochondria": "mitochondrion",
    "cilia": "cilium",
    "genera": "genus",
    "vertices": "vertex",
    "indices": "index",
    "matrices": "matrix",
    "appendices": "appendix",
    "media": "medium",
}

# Suffixes that mean "this token does NOT end in a plural -s" — used to
# protect words like "nervous", "focus", "axis", "stress" from being
# stem-stripped to garbage.
_NO_STRIP_SUFFIXES: Final[tuple[str, ...]] = ("ous", "us", "is", "ss")

# JATS <sec sec-type="..."> attribute values we treat as IMRaD. Keys are
# lowercased sec-type strings; values are the canonical label used by
# both the parser and the cache schema. JATS allows pipe-separated
# compound types like "materials|methods", which is why those appear as
# distinct keys rather than being computed.
_JATS_IMRAD_SECTYPES: Final[dict[str, str]] = {
    "intro": "introduction",
    "introduction": "introduction",
    "methods": "methods",
    "materials|methods": "methods",
    "subjects|methods": "methods",
    "results": "results",
    "discussion": "discussion",
    "conclusions": "discussion",
}

# Fallback when <sec sec-type> is absent: classify by the section's
# <title> text. Patterns are case-insensitive and anchored at the start
# so "Methods" matches but "Statistical methods" doesn't accidentally
# pull in something we shouldn't (statistical subsections live inside a
# parent Methods section that already classified upstream).
_JATS_TITLE_SYNONYMS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (re.compile(r"^\s*introduction\b", re.I), "introduction"),
    (re.compile(r"^\s*background\b", re.I), "introduction"),
    (
        re.compile(
            r"^\s*(materials\s+and\s+methods|subjects\s+and\s+methods|"
            r"experimental\s+procedures|methods)\b",
            re.I,
        ),
        "methods",
    ),
    (re.compile(r"^\s*(results|findings)\b", re.I), "results"),
    (re.compile(r"^\s*(discussion|conclusions?)\b", re.I), "discussion"),
)


# ---------------------------------------------------------------------------
# DATACLASSES
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PaperText:
    """Title + abstract (+ optional PMC full-text) for one paper."""

    pmid: str | None
    title: str
    abstract: str
    fulltext: str = ""

    @property
    def combined(self) -> str:
        return f"{self.title} {self.abstract} {self.fulltext}".strip()


@dataclass(slots=True, frozen=True)
class FulltextRecord:
    """PMC full-text harvest result for one PMID.

    ``pmcid is None`` doubles as the "no PMC mirror" sentinel — those
    records are cached on disk too, so we don't re-elink missing PMIDs
    on subsequent runs. Section fields default to "" for that case.
    """

    pmid: str
    pmcid: str | None
    introduction: str = ""
    methods: str = ""
    results: str = ""
    discussion: str = ""

    def as_text(self) -> str:
        return "\n\n".join(
            s
            for s in (self.introduction, self.methods, self.results, self.discussion)
            if s
        ).strip()


@dataclass(slots=True)
class KeywordScore:
    """A ranked candidate keyword."""

    term: str
    document_frequency: int
    total_count: int
    llr: float = 0.0


@dataclass(slots=True, frozen=True)
class MeshQualifier:
    term: str
    ui: str
    major: bool


@dataclass(slots=True, frozen=True)
class MeshDescriptor:
    term: str
    ui: str
    major: bool
    qualifiers: tuple[MeshQualifier, ...] = ()


@dataclass(slots=True)
class BaselineCounts:
    """Frozen PubMed background frequencies used as the LLR reference.

    Unigram/bigram/trigram keys are uniformly ``tuple[str, ...]`` so
    they line up with the foreground ranking keys; acronyms stay flat
    strings because the foreground detector treats acronyms as
    surface-form tokens (no stemming).
    """

    schema_version: int
    built_at: str
    params: dict[str, Any]
    total_docs: int
    unigrams: Counter[tuple[str, ...]]
    bigrams: Counter[tuple[str, ...]]
    trigrams: Counter[tuple[str, ...]]
    acronyms: Counter[str]
    total_unigrams: int
    total_bigrams: int
    total_trigrams: int
    total_acronyms: int


@dataclass(slots=True)
class DistillationResult:
    """The full ranked output for one corpus."""

    papers: int
    unigrams: list[KeywordScore]
    bigrams: list[KeywordScore]
    trigrams: list[KeywordScore]
    acronyms: list[KeywordScore]
    mesh_terms: list[KeywordScore] = field(default_factory=list)
    query_variants: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# XML PARSING (MODS)
# ---------------------------------------------------------------------------


def _element_text(elem: etree._Element | None) -> str:
    """Concatenate all text within an element (handles mixed content)."""
    if elem is None:
        return ""
    return "".join(t for t in elem.itertext() if isinstance(t, str)).strip()


def parse_mods_file(path: Path) -> PaperText | None:
    """Parse one MODS XML file into a PaperText.

    Anchors at the inner ``<mods>`` element so titles inside
    ``<relatedItem>`` (the journal) don't leak into the article title.
    """
    try:
        tree = etree.parse(str(path), parser=_SAFE_PARSER)
    except (etree.XMLSyntaxError, OSError) as e:
        logger.warning(f"XML parse error in {path.name}: {e}")
        return None

    root = tree.getroot()
    mods_el = root.find(f".//{_NS}mods")
    if mods_el is None:
        mods_el = root  # tolerate records lacking the <modsCollection> wrapper

    title_info = mods_el.find(f"./{_NS}titleInfo")
    title_el = title_info.find(f"./{_NS}title") if title_info is not None else None
    subtitle_el = (
        title_info.find(f"./{_NS}subTitle") if title_info is not None else None
    )
    abstract_el = mods_el.find(f"./{_NS}abstract")
    pmid_el = mods_el.find(f"./{_NS}identifier[@type='pubmed']")

    title_parts = [
        s for s in (_element_text(title_el), _element_text(subtitle_el)) if s
    ]
    title = ": ".join(title_parts)
    abstract = _element_text(abstract_el)

    if not title and not abstract:
        logger.debug(f"No title or abstract in {path.name}")
        return None

    return PaperText(
        pmid=_element_text(pmid_el) or None,
        title=title,
        abstract=abstract,
    )


def load_corpus(xml_dir: Path) -> list[PaperText]:
    """Parse every ``*.xml`` file in the directory into PaperText records."""
    if not xml_dir.exists() or not xml_dir.is_dir():
        raise FileNotFoundError(f"XML directory not found: {xml_dir}")

    files = sorted(xml_dir.glob("*.xml"))
    papers = [p for p in (parse_mods_file(f) for f in files) if p is not None]
    logger.info(
        f"Parsed {len(papers)} paper(s) from {len(files)} XML file(s) in {xml_dir}"
    )
    return papers


# ---------------------------------------------------------------------------
# TOKENIZATION & N-GRAMS
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> list[str]:
    return _TOKEN_PATTERN.findall(text)


def _is_content_token(token_lower: str) -> bool:
    """Reject stopwords, pure-numeric tokens, and tokens shorter than 3 chars."""
    if not token_lower:
        return False
    if token_lower in _STOPWORDS:
        return False
    if _NUMERIC_PATTERN.match(token_lower):
        return False
    return len(token_lower) >= MIN_TOKEN_LENGTH


def _ngrams(tokens: list[str], n: int) -> list[tuple[str, ...]]:
    if n <= 0 or len(tokens) < n:
        return []
    return [tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)]


# ---------------------------------------------------------------------------
# LEMMATIZATION
# ---------------------------------------------------------------------------


def stem_key(token: str) -> str:
    """Singular/stem form used as a hash key during aggregation.

    Conservative rule-based stemming: lookup irregulars, strip ``-ies →
    -y`` when the preceding char is a consonant, strip a trailing ``-s``
    unless the token ends in a non-plural suffix (``-ous``, ``-us``,
    ``-is``, ``-ss``). Aggregation hashes on this; display keeps the
    modal surface form so the output stays readable.
    """
    lower = token.lower()
    if lower in _IRREGULAR_PLURALS:
        return _IRREGULAR_PLURALS[lower]
    if len(lower) < 4:
        return lower
    if (
        lower.endswith("ies")
        and len(lower) > 4
        and lower[-4] not in "aeiou"
    ):
        return lower[:-3] + "y"
    if lower.endswith("s") and not any(
        lower.endswith(suf) for suf in _NO_STRIP_SUFFIXES
    ):
        stripped = lower[:-1]
        if len(stripped) >= MIN_TOKEN_LENGTH:
            return stripped
    return lower


# ---------------------------------------------------------------------------
# LLR RANKING
# ---------------------------------------------------------------------------


def _llr_score(a: float, b: float, c: float, d: float) -> float:
    """Dunning's log-likelihood ratio for a 2x2 contingency table.

    Cells get +0.5 Laplace smoothing so log(0) cannot occur. The result
    is the standard ``2 * Σ O log(O/E)`` formulation.
    """
    a, b, c, d = a + 0.5, b + 0.5, c + 0.5, d + 0.5
    n = a + b + c + d
    e_a = (a + b) * (a + c) / n
    e_b = (a + b) * (b + d) / n
    e_c = (c + d) * (a + c) / n
    e_d = (c + d) * (b + d) / n
    return 2.0 * (
        a * math.log(a / e_a)
        + b * math.log(b / e_b)
        + c * math.log(c / e_c)
        + d * math.log(d / e_d)
    )


def _rank_terms(
    fg_counts: Counter[Any],
    fg_doc_freq: Counter[Any],
    bg_counts: Counter[Any] | None,
    total_fg: int,
    total_bg: int | None,
    *,
    min_df: int,
    top_n: int,
    min_llr: float,
    display: Mapping[Any, str] | None = None,
) -> list[KeywordScore]:
    """Rank candidate terms by Dunning's LLR vs. background counts.

    Falls back to document-frequency ranking when no baseline is
    available — used for the ``--no-mesh`` baseline-less smoke path
    and during corpus exploration before the first baseline build.

    The ``display`` mapping converts the internal hash key (typically a
    tuple of stems) to the surface string shown to the user.
    """
    if bg_counts is None or total_bg is None or total_bg == 0:
        return _rank_terms_df(
            fg_counts,
            fg_doc_freq,
            min_df=min_df,
            top_n=top_n,
            display=display,
        )

    scored: list[KeywordScore] = []
    for key, fg in fg_counts.items():
        if fg_doc_freq[key] < min_df:
            continue
        bg = bg_counts.get(key, 0)
        # Sign filter: keep terms more frequent in foreground than baseline.
        if (fg / total_fg) <= (bg / total_bg):
            continue
        llr = _llr_score(fg, bg, total_fg - fg, total_bg - bg)
        if llr < min_llr:
            continue
        term = (
            display[key]
            if display is not None and key in display
            else _stringify(key)
        )
        scored.append(
            KeywordScore(
                term=term,
                document_frequency=fg_doc_freq[key],
                total_count=fg,
                llr=llr,
            )
        )

    scored.sort(key=lambda k: (-k.llr, -k.document_frequency, k.term))
    return scored[:top_n]


def _rank_terms_df(
    fg_counts: Counter[Any],
    fg_doc_freq: Counter[Any],
    *,
    min_df: int,
    top_n: int,
    display: Mapping[Any, str] | None = None,
) -> list[KeywordScore]:
    """Fallback ranking by document frequency — used when no baseline."""
    scored: list[KeywordScore] = []
    for key, fg in fg_counts.items():
        if fg_doc_freq[key] < min_df:
            continue
        term = (
            display[key]
            if display is not None and key in display
            else _stringify(key)
        )
        scored.append(
            KeywordScore(
                term=term,
                document_frequency=fg_doc_freq[key],
                total_count=fg,
            )
        )
    scored.sort(key=lambda k: (-k.document_frequency, -k.total_count, k.term))
    return scored[:top_n]


def _stringify(key: Any) -> str:
    """Stringify a hash key (single token or tuple of tokens) for display."""
    if isinstance(key, tuple):
        return " ".join(str(t) for t in key)
    return str(key)


# ---------------------------------------------------------------------------
# BASELINE CORPUS (one-time fetch + cache)
# ---------------------------------------------------------------------------


def _configure_entrez(
    *, email: str | None = None, api_key: str | None = None
) -> str | None:
    """Lazy NCBI Entrez configuration. Returns the active API key (or None).

    Reads ``ENTREZ_EMAIL`` / ``NCBI_API_KEY`` from the environment if
    not passed explicitly. ``python-dotenv`` is consulted via the
    caller's startup; we deliberately don't import it here to keep this
    function side-effect free for tests.
    """
    from Bio import Entrez  # local import — only when networking is requested

    resolved_email = email or os.getenv("ENTREZ_EMAIL", "")
    resolved_key = api_key or os.getenv("NCBI_API_KEY") or os.getenv("ENTREZ_KEY")
    if not resolved_email:
        raise RuntimeError(
            "ENTREZ_EMAIL is required for NCBI Entrez. Set it in .env. "
            "NCBI's policy requires a valid contact."
        )
    Entrez.email = resolved_email  # ty: ignore[invalid-assignment]
    Entrez.api_key = resolved_key  # ty: ignore[invalid-assignment]
    return resolved_key


def _ncbi_sleep(api_key: str | None) -> None:
    time.sleep(_NCBI_SLEEP_WITH_KEY if api_key else _NCBI_SLEEP_NO_KEY)


def _ncbi_retry(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Run an Entrez call with simple exponential-backoff retries."""
    last_exc: Exception | None = None
    total = len(_NCBI_RETRY_BACKOFF)
    for attempt, wait in enumerate(_NCBI_RETRY_BACKOFF):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — Entrez raises various I/O types
            last_exc = exc
            if attempt == total - 1:
                break  # don't sleep after the final failure
            logger.warning(
                f"NCBI call failed (attempt {attempt + 1}/{total}): {exc}; "
                f"retrying in {wait}s"
            )
            time.sleep(wait)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("NCBI retry loop exhausted with no exception captured")


def _extract_text_from_pubmed_record(article_el: etree._Element) -> tuple[str, str]:
    """Title + abstract text from one ``<PubmedArticle>``."""
    title_el = article_el.find(".//ArticleTitle")
    title = _element_text(title_el)
    abstract_parts = [
        _element_text(seg) for seg in article_el.findall(".//Abstract/AbstractText")
    ]
    abstract = " ".join(p for p in abstract_parts if p)
    return title, abstract


def _accumulate_paper_counts(
    text: str,
    *,
    uni: Counter[str],
    bi: Counter[tuple[str, ...]],
    tri: Counter[tuple[str, ...]],
    acros: Counter[str],
) -> None:
    """Tokenise + stem-hash + update n-gram + acronym counters in place.

    No stopword filtering at the corpus-build step — LLR will demote
    common terms automatically. Filtering here would bias the baseline.
    """
    raw_tokens = _tokenize(text)
    stems = [stem_key(t) for t in raw_tokens]

    for s in stems:
        uni[s] += 1

    for gram in _ngrams(stems, 2):
        bi[gram] += 1

    for gram in _ngrams(stems, 3):
        tri[gram] += 1

    for tok in raw_tokens:
        if (
            MIN_ACRONYM_LENGTH <= len(tok) <= MAX_ACRONYM_LENGTH
            and tok.isupper()
            and tok.lower() not in _STOPWORDS
        ):
            acros[tok] += 1


def build_baseline_cache(
    size: int,
    output_path: Path,
    *,
    pdat_range: str,
    email: str | None = None,
    api_key: str | None = None,
    batch_size: int = DEFAULT_BASELINE_BATCH,
) -> Path:
    """Fetch a random-ish PubMed sample and write the frequency cache.

    Uses ``Bio.Entrez.esearch`` with a broad English/journal-article
    query in the given PDAT range, sorted by recency, taking the first
    ``size`` PMIDs. Then ``efetch`` in batches and accumulates n-gram +
    acronym counts. Writes gzipped JSON to ``output_path``.
    """
    from Bio import Entrez

    resolved_key = _configure_entrez(email=email, api_key=api_key)

    if not _PDAT_RANGE_PATTERN.match(pdat_range):
        raise ValueError(
            f"pdat_range must be 'YYYY:YYYY' (e.g. '2020:2024'), "
            f"got {pdat_range!r}"
        )
    pdat_from, pdat_to = pdat_range.split(":", 1)
    query = (
        f'"english"[Language] AND "journal article"[Publication Type] '
        f'AND ("{pdat_from}"[PDAT] : "{pdat_to}"[PDAT])'
    )
    logger.info(
        f"Fetching baseline ({size} abstracts, PDAT={pdat_range})..."
    )

    handle = _ncbi_retry(
        Entrez.esearch,
        db="pubmed",
        term=query,
        retmax=size,
        sort="date",
        usehistory="n",
    )
    try:
        results = Entrez.read(handle)
    finally:
        handle.close()
    _ncbi_sleep(resolved_key)

    pmids = list(results.get("IdList", []))
    if not pmids:
        raise RuntimeError(
            f"PubMed esearch returned no PMIDs for query: {query[:80]}..."
        )

    uni: Counter[str] = Counter()
    bi: Counter[tuple[str, ...]] = Counter()
    tri: Counter[tuple[str, ...]] = Counter()
    acros: Counter[str] = Counter()
    total_docs = 0

    for start in range(0, len(pmids), batch_size):
        batch = pmids[start : start + batch_size]
        handle = _ncbi_retry(
            Entrez.efetch,
            db="pubmed",
            id=",".join(batch),
            rettype="abstract",
            retmode="xml",
        )
        try:
            raw = handle.read()
        finally:
            handle.close()
        _ncbi_sleep(resolved_key)

        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        try:
            root = etree.fromstring(raw, parser=_SAFE_PARSER)
        except etree.XMLSyntaxError as e:
            logger.warning(
                f"Baseline batch {start}-{start + len(batch)} parse error: {e}"
            )
            continue

        for article_el in root.findall(".//PubmedArticle"):
            title, abstract = _extract_text_from_pubmed_record(article_el)
            if not title and not abstract:
                continue
            total_docs += 1
            _accumulate_paper_counts(
                f"{title} {abstract}",
                uni=uni,
                bi=bi,
                tri=tri,
                acros=acros,
            )

        if (start // batch_size) % 5 == 4:
            logger.info(
                f"  baseline progress: {min(start + batch_size, len(pmids))}"
                f"/{len(pmids)} PMIDs"
            )

    logger.info(f"Baseline assembled from {total_docs} parseable abstract(s).")

    # Drop hapaxes from bigrams/trigrams to keep the cache file manageable.
    bi_filtered = Counter(
        {k: v for k, v in bi.items() if v >= BASELINE_NGRAM_MIN_COUNT}
    )
    tri_filtered = Counter(
        {k: v for k, v in tri.items() if v >= BASELINE_NGRAM_MIN_COUNT}
    )

    payload = {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "built_at": _dt.datetime.now(_dt.UTC).isoformat(),
        "params": {
            "size_requested": size,
            "pdat_range": pdat_range,
            "ngram_min_count": BASELINE_NGRAM_MIN_COUNT,
            "query": query,
        },
        "total_docs": total_docs,
        "total_unigrams": sum(uni.values()),
        "total_bigrams": sum(bi.values()),  # totals from unfiltered counts
        "total_trigrams": sum(tri.values()),
        "total_acronyms": sum(acros.values()),
        "unigrams": dict(uni),
        "bigrams": {" ".join(k): v for k, v in bi_filtered.items()},
        "trigrams": {" ".join(k): v for k, v in tri_filtered.items()},
        "acronyms": dict(acros),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Tmp+rename so an interrupted write can't leave a half-written gzip
    # that subsequent loads would reject as corrupt.
    tmp_path = output_path.with_name(output_path.name + ".tmp")
    try:
        with gzip.open(tmp_path, "wt", encoding="utf-8") as gz:
            json.dump(payload, gz)
        tmp_path.replace(output_path)
    except Exception:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise
    logger.info(f"Wrote baseline cache to {output_path}")
    return output_path


def load_baseline_cache(path: Path) -> BaselineCounts:
    """Read a baseline cache; raise if missing or schema-mismatched."""
    if not path.exists():
        raise FileNotFoundError(
            f"Baseline cache not found at {path}. Build it first with:\n"
            f"  python {Path(sys.argv[0]).name} --build-baseline"
        )
    with gzip.open(path, "rt", encoding="utf-8") as gz:
        payload = json.load(gz)

    version = payload.get("schema_version")
    if version != BASELINE_SCHEMA_VERSION:
        raise RuntimeError(
            f"Baseline cache schema {version} != expected "
            f"{BASELINE_SCHEMA_VERSION}. Rebuild with --build-baseline."
        )

    built_at_str = str(payload.get("built_at", ""))
    try:
        built_at = _dt.datetime.fromisoformat(built_at_str)
    except ValueError:
        built_at = _dt.datetime.now(_dt.UTC)
    age_days = (_dt.datetime.now(_dt.UTC) - built_at).days
    if age_days > BASELINE_STALE_DAYS:
        logger.warning(
            f"Baseline cache is {age_days} days old (> {BASELINE_STALE_DAYS}). "
            f"Consider rebuilding with --build-baseline."
        )

    return BaselineCounts(
        schema_version=version,
        built_at=built_at_str,
        params=dict(payload.get("params", {})),
        total_docs=int(payload.get("total_docs", 0)),
        # Foreground n=1 ranking keys n-grams as `(stem,)` tuples; the
        # cache stores them flat as strings for compact JSON. Wrap on load.
        unigrams=Counter(
            {(k,): v for k, v in payload.get("unigrams", {}).items()}
        ),
        bigrams=Counter(
            {tuple(k.split(" ")): v for k, v in payload.get("bigrams", {}).items()}
        ),
        trigrams=Counter(
            {tuple(k.split(" ")): v for k, v in payload.get("trigrams", {}).items()}
        ),
        acronyms=Counter(payload.get("acronyms", {})),
        total_unigrams=int(payload.get("total_unigrams", 0)),
        total_bigrams=int(payload.get("total_bigrams", 0)),
        total_trigrams=int(payload.get("total_trigrams", 0)),
        total_acronyms=int(payload.get("total_acronyms", 0)),
    )


# ---------------------------------------------------------------------------
# MESH HARVEST
# ---------------------------------------------------------------------------


def parse_pubmed_xml_for_mesh(xml_bytes: bytes) -> dict[str, list[MeshDescriptor]]:
    """Parse PubMed efetch XML, returning ``{pmid: [MeshDescriptor, ...]}``.

    Raises ``etree.XMLSyntaxError`` on malformed XML so the caller can
    distinguish a parse failure (skip cache + retry next run) from a
    genuinely empty response. Used by ``fetch_mesh_terms`` and exposed
    directly for unit tests on a committed fixture.
    """
    root = etree.fromstring(xml_bytes, parser=_SAFE_PARSER)

    out: dict[str, list[MeshDescriptor]] = {}
    for article in root.findall(".//PubmedArticle"):
        pmid_el = article.find(".//MedlineCitation/PMID")
        pmid = _element_text(pmid_el)
        if not pmid:
            continue
        descriptors: list[MeshDescriptor] = []
        for heading in article.findall(".//MeshHeadingList/MeshHeading"):
            d_el = heading.find("./DescriptorName")
            if d_el is None:
                continue
            d_term = _element_text(d_el)
            if not d_term:
                continue
            d_ui = d_el.get("UI", "")
            d_major = d_el.get("MajorTopicYN", "N") == "Y"
            qualifiers: list[MeshQualifier] = []
            for q_el in heading.findall("./QualifierName"):
                q_term = _element_text(q_el)
                if not q_term:
                    continue
                qualifiers.append(
                    MeshQualifier(
                        term=q_term,
                        ui=q_el.get("UI", ""),
                        major=q_el.get("MajorTopicYN", "N") == "Y",
                    )
                )
            descriptors.append(
                MeshDescriptor(
                    term=d_term,
                    ui=d_ui,
                    major=d_major,
                    qualifiers=tuple(qualifiers),
                )
            )
        out[pmid] = descriptors
    return out


def _descriptors_to_jsonable(items: list[MeshDescriptor]) -> list[dict[str, Any]]:
    return [
        {
            "term": d.term,
            "ui": d.ui,
            "major": d.major,
            "qualifiers": [
                {"term": q.term, "ui": q.ui, "major": q.major}
                for q in d.qualifiers
            ],
        }
        for d in items
    ]


def _descriptors_from_jsonable(items: list[dict[str, Any]]) -> list[MeshDescriptor]:
    return [
        MeshDescriptor(
            term=str(d["term"]),
            ui=str(d.get("ui", "")),
            major=bool(d.get("major", False)),
            qualifiers=tuple(
                MeshQualifier(
                    term=str(q["term"]),
                    ui=str(q.get("ui", "")),
                    major=bool(q.get("major", False)),
                )
                for q in d.get("qualifiers", [])
            ),
        )
        for d in items
    ]


def fetch_mesh_terms(
    pmids: Iterable[str],
    cache_dir: Path,
    *,
    email: str | None = None,
    api_key: str | None = None,
    batch_size: int = DEFAULT_MESH_BATCH,
    fetcher: Any = None,
) -> dict[str, list[MeshDescriptor]]:
    """Return ``{pmid: [MeshDescriptor, ...]}`` for the given PMIDs.

    Reads from per-PMID JSON cache where present; for missing PMIDs,
    calls Entrez.efetch in batches and writes the results back to the
    cache. Network failures are logged and skipped — the returned map
    contains every PMID that was either cached or successfully fetched.

    ``fetcher`` is an injection point for tests: a callable taking a
    list of PMIDs and returning the raw efetch XML bytes. Default uses
    ``Bio.Entrez.efetch``.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    pmid_list = [str(p) for p in pmids if p]
    out: dict[str, list[MeshDescriptor]] = {}
    missing: list[str] = []

    for pmid in pmid_list:
        cache_path = cache_dir / f"{pmid}.json"
        if cache_path.exists():
            try:
                with cache_path.open(encoding="utf-8") as f:
                    cached = json.load(f)
                out[pmid] = _descriptors_from_jsonable(
                    cached.get("descriptors", [])
                )
            except (
                json.JSONDecodeError,
                OSError,
                AttributeError,
                TypeError,
                KeyError,
            ) as e:
                # AttributeError/TypeError catch JSON that parses but isn't
                # a dict; KeyError catches descriptors missing the required
                # ``term`` field.
                logger.warning(
                    f"Corrupt MeSH cache for PMID {pmid}: {e}; will refetch"
                )
                missing.append(pmid)
        else:
            missing.append(pmid)

    if not missing:
        return out

    if fetcher is None:
        resolved_key = _configure_entrez(email=email, api_key=api_key)
        from Bio import Entrez

        def _default_fetcher(batch: list[str]) -> bytes:
            handle = _ncbi_retry(
                Entrez.efetch,
                db="pubmed",
                id=",".join(batch),
                rettype="medline",
                retmode="xml",
            )
            try:
                raw = handle.read()
            finally:
                handle.close()
            _ncbi_sleep(resolved_key)
            return raw if isinstance(raw, bytes) else str(raw).encode("utf-8")

        fetcher = _default_fetcher

    for start in range(0, len(missing), batch_size):
        batch = missing[start : start + batch_size]
        try:
            raw = fetcher(batch)
        except Exception as e:  # noqa: BLE001 — surface any fetch failure
            logger.warning(
                f"MeSH efetch batch {start}-{start + len(batch)} failed: {e}"
            )
            continue
        try:
            parsed = parse_pubmed_xml_for_mesh(raw)
        except etree.XMLSyntaxError as e:
            # Don't cache anything for this batch — a poisoned cache of
            # empty descriptors would suppress retries indefinitely.
            logger.warning(
                f"MeSH batch {start}-{start + len(batch)} XML parse error: "
                f"{e}; skipping cache (will retry next run)"
            )
            continue
        for pmid in batch:
            descriptors = parsed.get(pmid, [])
            out[pmid] = descriptors
            cache_path = cache_dir / f"{pmid}.json"
            try:
                with cache_path.open("w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "pmid": pmid,
                            "descriptors": _descriptors_to_jsonable(descriptors),
                            "fetched_at": _dt.datetime.now(_dt.UTC).isoformat(),
                        },
                        f,
                        ensure_ascii=False,
                    )
            except OSError as e:
                logger.warning(f"Could not write MeSH cache for PMID {pmid}: {e}")

    return out


def aggregate_mesh(
    pmid_to_descriptors: Mapping[str, list[MeshDescriptor]],
    *,
    top_n: int,
) -> list[KeywordScore]:
    """Rank MeSH descriptors by DF across papers; weight major topics 2x."""
    doc_freq: Counter[str] = Counter()
    weight: Counter[str] = Counter()
    for descriptors in pmid_to_descriptors.values():
        seen: set[str] = set()
        for d in descriptors:
            seen.add(d.term)
            weight[d.term] += 2 if d.major else 1
        for term in seen:
            doc_freq[term] += 1
    scored = [
        KeywordScore(
            term=term,
            document_frequency=doc_freq[term],
            total_count=weight[term],
        )
        for term in doc_freq
    ]
    scored.sort(key=lambda k: (-k.document_frequency, -k.total_count, k.term))
    return scored[:top_n]


# ---------------------------------------------------------------------------
# PMC FULLTEXT RETRIEVAL
# ---------------------------------------------------------------------------


def _classify_jats_section(sec: etree._Element) -> str | None:
    """Map one JATS ``<sec>`` element to an IMRaD label, or None.

    Tries ``@sec-type`` first (the explicit JATS markup), then falls
    back to ``<title>`` text matching against the synonym table. Returns
    None when neither pathway matches — those sections (typically
    Acknowledgments, Author Contributions, References, Supplementary)
    are intentionally dropped from the foreground corpus.
    """
    sectype = (sec.get("sec-type") or "").lower()
    if sectype and sectype in _JATS_IMRAD_SECTYPES:
        return _JATS_IMRAD_SECTYPES[sectype]
    title_el = sec.find(f"./{_NS}title")
    if title_el is None:
        return None
    title_text = _element_text(title_el)
    for pattern, label in _JATS_TITLE_SYNONYMS:
        if pattern.search(title_text):
            return label
    return None


def parse_jats_for_sections(xml_bytes: bytes) -> dict[str, str]:
    """Extract IMRaD section bodies from PMC JATS XML.

    Walks top-level ``<body>/<sec>`` elements (descendant ``<p>`` text
    is collected within each, so nested subsections are folded into
    their parent's label). When the same label appears multiple times
    in one article — e.g. two ``<sec sec-type="methods">`` blocks for
    "Materials and Methods" and "Statistical Methods" — they're joined
    with a blank line between.

    Returns a 4-key dict (introduction/methods/results/discussion);
    missing labels hold "". Raises ``etree.XMLSyntaxError`` on
    malformed XML so the caller can distinguish parse failures (skip
    cache + retry next run) from genuinely empty bodies.
    """
    root = etree.fromstring(xml_bytes, parser=_SAFE_PARSER)
    body = root.find(f".//{_NS}body")
    if body is None:
        return {"introduction": "", "methods": "", "results": "", "discussion": ""}

    parts_by_label: dict[str, list[str]] = {}
    for sec in body.findall(f"./{_NS}sec"):
        label = _classify_jats_section(sec)
        if label is None:
            continue
        paragraphs: list[str] = []
        for p in sec.findall(f".//{_NS}p"):
            # Indentation in source JATS XML leaks newlines + spaces into
            # paragraph text (e.g. "white matter\n      injury").
            # Collapse runs of whitespace to single spaces inside each
            # paragraph; paragraph boundaries are re-introduced by the
            # "\n\n".join below.
            text = re.sub(r"\s+", " ", _element_text(p))
            if text:
                paragraphs.append(text)
        if paragraphs:
            parts_by_label.setdefault(label, []).append("\n\n".join(paragraphs))

    return {
        "introduction": "\n\n".join(parts_by_label.get("introduction", [])),
        "methods": "\n\n".join(parts_by_label.get("methods", [])),
        "results": "\n\n".join(parts_by_label.get("results", [])),
        "discussion": "\n\n".join(parts_by_label.get("discussion", [])),
    }


def _read_fulltext_cache(cache_dir: Path, pmid: str) -> FulltextRecord | None:
    """Return the cached record for ``pmid``, or None if absent/corrupt.

    Schema-version mismatch and JSON-decode errors log a warning and
    return None so the caller can refetch (same recovery semantics as
    the MeSH cache).
    """
    cache_path = cache_dir / f"{pmid}.json"
    if not cache_path.exists():
        return None
    try:
        with cache_path.open(encoding="utf-8") as f:
            payload = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Corrupt fulltext cache for PMID {pmid}: {e}; will refetch")
        return None
    if payload.get("schema_version") != FULLTEXT_SCHEMA_VERSION:
        logger.warning(
            f"Fulltext cache schema mismatch for PMID {pmid} "
            f"({payload.get('schema_version')!r} != {FULLTEXT_SCHEMA_VERSION}); "
            f"will refetch"
        )
        return None
    try:
        sections = payload.get("sections") or {}
        return FulltextRecord(
            pmid=str(payload["pmid"]),
            pmcid=(str(payload["pmcid"]) if payload.get("pmcid") else None),
            introduction=str(sections.get("introduction", "")),
            methods=str(sections.get("methods", "")),
            results=str(sections.get("results", "")),
            discussion=str(sections.get("discussion", "")),
        )
    except (KeyError, TypeError, AttributeError) as e:
        logger.warning(f"Malformed fulltext cache for PMID {pmid}: {e}; will refetch")
        return None


def _write_fulltext_cache(cache_dir: Path, record: FulltextRecord) -> None:
    """Persist one ``FulltextRecord`` as JSON. Logs and continues on OSError."""
    cache_path = cache_dir / f"{record.pmid}.json"
    payload = {
        "schema_version": FULLTEXT_SCHEMA_VERSION,
        "pmid": record.pmid,
        "pmcid": record.pmcid,
        "sections": {
            "introduction": record.introduction,
            "methods": record.methods,
            "results": record.results,
            "discussion": record.discussion,
        },
        "fetched_at": _dt.datetime.now(_dt.UTC).isoformat(),
    }
    try:
        with cache_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except OSError as e:
        logger.warning(
            f"Could not write fulltext cache for PMID {record.pmid}: {e}"
        )


def _default_pmids_to_pmcids(
    pmids: list[str], *, api_key: str | None
) -> dict[str, str | None]:
    """Resolve a batch of PMIDs to PMCIDs in one ``Entrez.elink`` round-trip.

    NCBI's ``elink`` accepts a list of source ids; the response is a
    list of LinkSets, each carrying its source PMID in ``IdList`` and
    the linked PMC id (when one exists) in
    ``LinkSetDb[].Link[].Id``. Batching collapses N round-trips +
    N ``_ncbi_sleep`` pauses into one of each, which is the bulk of the
    first-run wall time at corpus sizes above a handful.

    Returns ``{pmid: pmcid_or_None}`` for every PMID in the input.
    """
    from Bio import Entrez

    handle = _ncbi_retry(
        Entrez.elink,
        dbfrom="pubmed",
        db="pmc",
        id=pmids,
        linkname="pubmed_pmc",
    )
    try:
        records = Entrez.read(handle)
    finally:
        handle.close()
    _ncbi_sleep(api_key)

    out: dict[str, str | None] = dict.fromkeys(pmids)
    for record in records or []:
        id_list = record.get("IdList", []) or []
        if not id_list:
            continue
        source_pmid = str(id_list[0])
        for linkset in record.get("LinkSetDb", []) or []:
            for link in linkset.get("Link", []) or []:
                linked_id = link.get("Id", "")
                if linked_id:
                    out[source_pmid] = f"PMC{linked_id}"
                    break
            if out.get(source_pmid):
                break
    return out


def _default_fetch_pmc_jats(pmcid: str, *, api_key: str | None) -> bytes | None:
    """Fetch JATS XML for a PMCID via ``Bio.Entrez.efetch(db="pmc")``."""
    from Bio import Entrez

    # PMC efetch accepts either the bare numeric id or the PMC-prefixed
    # form; strip the prefix for compatibility with older biopython.
    numeric = pmcid.removeprefix("PMC") if pmcid.startswith("PMC") else pmcid

    handle = _ncbi_retry(
        Entrez.efetch,
        db="pmc",
        id=numeric,
        rettype="xml",
        retmode="xml",
    )
    try:
        raw = handle.read()
    finally:
        handle.close()
    _ncbi_sleep(api_key)

    if isinstance(raw, str):
        return raw.encode("utf-8")
    return raw if raw else None


def fetch_fulltext_batch(
    pmids: Iterable[str],
    cache_dir: Path,
    *,
    email: str | None = None,
    api_key: str | None = None,
    fetcher_elink: Any = None,
    fetcher_efetch: Any = None,
) -> dict[str, FulltextRecord]:
    """Return ``{pmid: FulltextRecord}`` for the given PMIDs.

    Reads from per-PMID JSON cache when present; for missing PMIDs,
    resolves PMID->PMCID via a single batched elink, then fetches JATS
    via efetch for each PMID with a PMC mirror, writing each result
    back to the cache. Negative results (no PMC mirror) are cached too
    so we don't re-elink them every run. Network failures log a warning
    and skip that PMID (no cache entry, will retry next run).

    ``fetcher_elink`` is a callable ``(list[str]) -> dict[str, str |
    None]``; ``fetcher_efetch`` is ``(pmcid: str) -> bytes | None``.
    Both are test-injection points; when None, Entrez is configured and
    the matching ``_default_*`` helper is used.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    pmid_list = [str(p) for p in pmids if p]
    out: dict[str, FulltextRecord] = {}
    missing: list[str] = []

    for pmid in pmid_list:
        cached = _read_fulltext_cache(cache_dir, pmid)
        if cached is not None:
            out[pmid] = cached
        else:
            missing.append(pmid)

    if not missing:
        return out

    resolved_key = api_key
    if fetcher_elink is None or fetcher_efetch is None:
        resolved_key = _configure_entrez(email=email, api_key=api_key)

    if fetcher_elink is None:

        def _default_elink(batch: list[str]) -> dict[str, str | None]:
            return _default_pmids_to_pmcids(batch, api_key=resolved_key)

        fetcher_elink = _default_elink

    if fetcher_efetch is None:

        def _default_efetch(pmcid: str) -> bytes | None:
            return _default_fetch_pmc_jats(pmcid, api_key=resolved_key)

        fetcher_efetch = _default_efetch

    try:
        pmid_to_pmcid = fetcher_elink(missing)
    except Exception as e:  # noqa: BLE001 — Entrez raises various I/O types
        logger.warning(f"elink batch failed for {len(missing)} PMIDs: {e}")
        return out

    for pmid in missing:
        pmcid = pmid_to_pmcid.get(pmid)
        if not pmcid:
            record = FulltextRecord(pmid=pmid, pmcid=None)
            _write_fulltext_cache(cache_dir, record)
            out[pmid] = record
            continue

        try:
            xml_bytes = fetcher_efetch(pmcid)
        except Exception as e:  # noqa: BLE001 — Entrez raises various I/O types
            logger.warning(f"efetch failed for PMID {pmid} (PMCID {pmcid}): {e}")
            continue

        if not xml_bytes:
            logger.warning(f"Empty efetch response for PMID {pmid} (PMCID {pmcid})")
            continue

        try:
            sections = parse_jats_for_sections(xml_bytes)
        except etree.XMLSyntaxError as e:
            # Don't cache: a poisoned cache of empty sections would
            # suppress retries indefinitely (same reasoning as the MeSH
            # batch's XML-error handling).
            logger.warning(
                f"JATS parse error for PMID {pmid} (PMCID {pmcid}): {e}; "
                f"skipping cache (will retry next run)"
            )
            continue

        record = FulltextRecord(pmid=pmid, pmcid=pmcid, **sections)
        _write_fulltext_cache(cache_dir, record)
        out[pmid] = record

    return out


# ---------------------------------------------------------------------------
# DISTILLATION
# ---------------------------------------------------------------------------


def _build_display_map(
    paper_stem_token_pairs: list[list[tuple[str, str]]],
    n: int,
) -> dict[tuple[str, ...], str]:
    """Map each stem-ngram-key to its modal surface n-gram form.

    Aggregates by stem so plural variants share a count, but displays
    the most-frequently-observed surface form (so output reads as
    English, not stems).
    """
    surface_counts: dict[tuple[str, ...], Counter[tuple[str, ...]]] = {}
    for tokens in paper_stem_token_pairs:
        if len(tokens) < n:
            continue
        for i in range(len(tokens) - n + 1):
            stems = tuple(s for s, _ in tokens[i : i + n])
            surfaces = tuple(t for _, t in tokens[i : i + n])
            surface_counts.setdefault(stems, Counter())[surfaces] += 1
    return {
        stems: " ".join(surfaces.most_common(1)[0][0])
        for stems, surfaces in surface_counts.items()
    }


def _foreground_counts_for(
    paper_stem_token_pairs: list[list[tuple[str, str]]],
    n: int,
    *,
    filter_content: bool,
) -> tuple[Counter[tuple[str, ...]], Counter[tuple[str, ...]]]:
    """Compute (per-paper-summed counts, document-frequency) for n-grams.

    If ``filter_content`` is set, an n-gram is dropped when any token
    fails the stopword/length/numeric filter — applied to foreground
    but never to the baseline (so LLR sees consistent denominators).
    Both stem and surface form are checked against ``_STOPWORDS``:
    stemming collapses plural-only section labels ("results"→"result")
    to a stem that isn't a stopword, so checking the surface too is
    what keeps the bare "Methods:"/"Results:" prefixes filtered.
    """
    tf: Counter[tuple[str, ...]] = Counter()
    df: Counter[tuple[str, ...]] = Counter()
    for tokens in paper_stem_token_pairs:
        if len(tokens) < n:
            continue
        clean = []
        for i in range(len(tokens) - n + 1):
            pair_slice = tokens[i : i + n]
            stems = tuple(s for s, _ in pair_slice)
            if filter_content and not all(
                _is_content_token(s) and t not in _STOPWORDS
                for s, t in pair_slice
            ):
                continue
            clean.append(stems)
        tf.update(clean)
        df.update(set(clean))
    return tf, df


def _foreground_acronyms(
    papers: list[PaperText],
) -> tuple[Counter[str], Counter[str]]:
    """Detect ALL-CAPS short tokens — returns (TF, DF) across papers."""
    tf: Counter[str] = Counter()
    df: Counter[str] = Counter()
    for paper in papers:
        tokens = _tokenize(paper.combined)
        in_paper = {
            tok
            for tok in tokens
            if MIN_ACRONYM_LENGTH <= len(tok) <= MAX_ACRONYM_LENGTH
            and tok.isupper()
            and tok.lower() not in _STOPWORDS
        }
        tf.update(tok for tok in tokens if tok in in_paper)
        df.update(in_paper)
    return tf, df


def distill_keywords(
    papers: list[PaperText],
    *,
    baseline: BaselineCounts | None,
    top_n: int = DEFAULT_TOP_N,
    min_df: int = DEFAULT_MIN_DF,
    min_llr: float = DEFAULT_MIN_LLR,
    mesh_descriptors: Mapping[str, list[MeshDescriptor]] | None = None,
    mesh_top: int = DEFAULT_MESH_TOP,
    phrase_top: int = DEFAULT_PHRASE_TOP,
) -> DistillationResult:
    """Rank distinctive keywords from a corpus, optionally with MeSH."""
    paper_stem_token_pairs: list[list[tuple[str, str]]] = [
        [(stem_key(t), t.lower()) for t in _tokenize(p.combined)] for p in papers
    ]

    # Pair each n-gram size with its matching baseline counter + total.
    # Replaces three parallel if-ladders that mapped n to attributes.
    baseline_by_n: tuple[tuple[Counter[Any] | None, int | None], ...] = (
        (
            (baseline.unigrams, baseline.total_unigrams),
            (baseline.bigrams, baseline.total_bigrams),
            (baseline.trigrams, baseline.total_trigrams),
        )
        if baseline is not None
        else ((None, None), (None, None), (None, None))
    )

    rankings: dict[int, list[KeywordScore]] = {}
    for n in (1, 2, 3):
        tf, df = _foreground_counts_for(
            paper_stem_token_pairs, n, filter_content=True
        )
        display = _build_display_map(paper_stem_token_pairs, n)
        bg_counts, total_bg = baseline_by_n[n - 1]
        rankings[n] = _rank_terms(
            tf,
            df,
            bg_counts=bg_counts,
            total_fg=sum(tf.values()),
            total_bg=total_bg,
            min_df=min_df,
            top_n=top_n,
            min_llr=min_llr,
            display=display,
        )

    # Acronyms — surface-form keys (no stemming).
    acro_tf, acro_df = _foreground_acronyms(papers)
    acronyms = _rank_terms(
        acro_tf,
        acro_df,
        bg_counts=baseline.acronyms if baseline is not None else None,
        total_fg=sum(acro_tf.values()),
        total_bg=baseline.total_acronyms if baseline is not None else None,
        min_df=min_df,
        top_n=top_n,
        min_llr=min_llr,
        display=None,
    )

    mesh_terms: list[KeywordScore] = []
    if mesh_descriptors:
        mesh_terms = aggregate_mesh(mesh_descriptors, top_n=mesh_top)

    result = DistillationResult(
        papers=len(papers),
        unigrams=rankings[1],
        bigrams=rankings[2],
        trigrams=rankings[3],
        acronyms=acronyms,
        mesh_terms=mesh_terms,
    )
    result.query_variants = build_query_variants(
        result, mesh_top=mesh_top, phrase_top=phrase_top
    )
    return result


# ---------------------------------------------------------------------------
# OUTPUT FORMATTING
# ---------------------------------------------------------------------------


def _merged_phrases(result: DistillationResult) -> list[KeywordScore]:
    """Bigrams + trigrams ranked together — used for the T/A query."""
    return sorted(
        result.trigrams + result.bigrams,
        key=lambda k: (-k.llr, -k.document_frequency, k.term),
    )


def format_titleabstract_query(scores: list[KeywordScore], top: int) -> str:
    """OR-joined ``[Title/Abstract]`` Boolean fragment."""
    if not scores or top <= 0:
        return ""
    return " OR ".join(f'"{s.term}"[Title/Abstract]' for s in scores[:top])


def format_mesh_query(scores: list[KeywordScore], top: int) -> str:
    """OR-joined ``[MeSH Terms]`` Boolean fragment."""
    if not scores or top <= 0:
        return ""
    return " OR ".join(f'"{s.term}"[MeSH Terms]' for s in scores[:top])


def format_structured_query(
    mesh_scores: list[KeywordScore],
    phrase_scores: list[KeywordScore],
    *,
    mesh_top: int,
    phrase_top: int,
) -> str:
    """``(mesh OR ...) AND ("phrase" OR ...)`` Boolean fragment."""
    mesh_clause = format_mesh_query(mesh_scores, mesh_top)
    phrase_clause = format_titleabstract_query(phrase_scores, phrase_top)
    if mesh_clause and phrase_clause:
        return f"({mesh_clause}) AND ({phrase_clause})"
    if mesh_clause:
        return f"({mesh_clause})"
    if phrase_clause:
        return f"({phrase_clause})"
    return ""


def build_query_variants(
    result: DistillationResult,
    *,
    mesh_top: int,
    phrase_top: int,
) -> dict[str, str]:
    phrases = _merged_phrases(result)
    return {
        "structured": format_structured_query(
            result.mesh_terms, phrases, mesh_top=mesh_top, phrase_top=phrase_top
        ),
        "mesh": format_mesh_query(result.mesh_terms, mesh_top),
        "titleabstract": format_titleabstract_query(phrases, phrase_top),
    }


def _print_section(
    title: str,
    scores: list[KeywordScore],
    stream: TextIO,
    *,
    show_llr: bool = True,
) -> None:
    print(f"\n--- {title} (showing {len(scores)}) ---", file=stream)
    if not scores:
        print("(none)", file=stream)
        return
    width = max(len(s.term) for s in scores)
    header = f"{'TERM'.ljust(width)}  DF  TF"
    if show_llr:
        header += "      LLR"
    print(header, file=stream)
    for s in scores:
        row = f"{s.term.ljust(width)}  {s.document_frequency:>2}  {s.total_count:>3}"
        if show_llr:
            row += f"  {s.llr:>7.2f}"
        print(row, file=stream)


def write_text_report(
    result: DistillationResult,
    *,
    mesh_top: int,
    phrase_top: int,
    query_format: str,
    stream: TextIO,
) -> None:
    print(f"\nDistilled keywords from {result.papers} paper(s).", file=stream)
    if result.mesh_terms:
        _print_section(
            "Top MeSH headings", result.mesh_terms, stream, show_llr=False
        )
    _print_section("Top distinctive phrases (bigrams)", result.bigrams, stream)
    _print_section("Top distinctive phrases (trigrams)", result.trigrams, stream)
    _print_section("Top distinctive unigrams", result.unigrams, stream)
    _print_section("Acronyms", result.acronyms, stream)

    variants = result.query_variants
    formats = (
        list(_QUERY_FORMAT_VARIANTS) if query_format == "all" else [query_format]
    )
    print("\n--- Suggested PubMed query ---", file=stream)
    for fmt in formats:
        q = variants.get(fmt, "")
        if not q:
            continue
        print(f"\n[{fmt}]", file=stream)
        print(q, file=stream)


def to_json(
    result: DistillationResult,
    *,
    indent: int = 2,
) -> str:
    def _scores(xs: list[KeywordScore]) -> list[dict[str, Any]]:
        return [
            {
                "term": s.term,
                "document_frequency": s.document_frequency,
                "total_count": s.total_count,
                "llr": round(s.llr, 4),
            }
            for s in xs
        ]

    return json.dumps(
        {
            "papers": result.papers,
            "unigrams": _scores(result.unigrams),
            "bigrams": _scores(result.bigrams),
            "trigrams": _scores(result.trigrams),
            "acronyms": _scores(result.acronyms),
            "mesh_terms": _scores(result.mesh_terms),
            "query_variants": result.query_variants,
        },
        indent=indent,
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _non_negative_int(value: str) -> int:
    """argparse type: parse an int and reject negatives.

    Without this, ``--top-n -5`` silently slips through Python's slice
    semantics (``scores[:-5]``) and returns "all but the last 5" instead
    of erroring — a confusing failure mode.
    """
    try:
        parsed = int(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"invalid int: {value!r}") from e
    if parsed < 0:
        raise argparse.ArgumentTypeError(
            f"value must be non-negative, got {parsed}"
        )
    return parsed


def _non_negative_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"invalid float: {value!r}") from e
    if parsed < 0:
        raise argparse.ArgumentTypeError(
            f"value must be non-negative, got {parsed}"
        )
    return parsed


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Distill PubMed-API-ready keywords from MODS bibliography XML "
            "with LLR-vs-PubMed-baseline ranking and MeSH harvesting."
        )
    )
    parser.add_argument(
        "--xml-dir",
        type=Path,
        default=DEFAULT_XML_DIR,
        help=f"Directory of MODS XML files (default: {DEFAULT_XML_DIR})",
    )
    parser.add_argument(
        "--top-n",
        type=_non_negative_int,
        default=DEFAULT_TOP_N,
        help=f"Keywords kept per n-gram size (default: {DEFAULT_TOP_N})",
    )
    parser.add_argument(
        "--min-df",
        type=_non_negative_int,
        default=DEFAULT_MIN_DF,
        help=(
            "Minimum document frequency to keep a keyword "
            f"(default: {DEFAULT_MIN_DF})"
        ),
    )
    parser.add_argument(
        "--min-llr",
        type=_non_negative_float,
        default=DEFAULT_MIN_LLR,
        help=(
            "Minimum log-likelihood ratio for inclusion "
            f"(default: {DEFAULT_MIN_LLR:.2f}, chi-square p<0.01 df=1)"
        ),
    )
    parser.add_argument(
        "--phrase-top",
        type=_non_negative_int,
        default=DEFAULT_PHRASE_TOP,
        help=(
            "Phrases included in the suggested query "
            f"(default: {DEFAULT_PHRASE_TOP})"
        ),
    )
    parser.add_argument(
        "--query-top",
        type=_non_negative_int,
        default=None,
        help="Deprecated alias for --phrase-top.",
    )
    parser.add_argument(
        "--mesh-top",
        type=_non_negative_int,
        default=DEFAULT_MESH_TOP,
        help=(
            "MeSH headings included in the suggested query "
            f"(default: {DEFAULT_MESH_TOP})"
        ),
    )
    parser.add_argument(
        "--query-format",
        choices=_QUERY_FORMATS,
        default="all",
        help="Which query variant(s) to emit (default: all)",
    )
    parser.add_argument(
        "--no-mesh",
        action="store_true",
        help="Skip MeSH harvest; emit only the Title/Abstract query variant.",
    )
    parser.add_argument(
        "--no-fulltext",
        action="store_true",
        help="Skip PMC full-text harvest; use title+abstract only.",
    )
    parser.add_argument(
        "--fulltext-cache",
        type=Path,
        default=DEFAULT_FULLTEXT_CACHE_DIR,
        help=(
            "Per-PMID PMC full-text cache dir "
            f"(default: {DEFAULT_FULLTEXT_CACHE_DIR})"
        ),
    )
    parser.add_argument(
        "--baseline-cache",
        type=Path,
        default=DEFAULT_BASELINE_CACHE,
        help=f"Baseline cache path (default: {DEFAULT_BASELINE_CACHE})",
    )
    parser.add_argument(
        "--mesh-cache",
        type=Path,
        default=DEFAULT_MESH_CACHE_DIR,
        help=f"Per-PMID MeSH cache dir (default: {DEFAULT_MESH_CACHE_DIR})",
    )
    parser.add_argument(
        "--build-baseline",
        action="store_true",
        help="Build the PubMed baseline cache and exit.",
    )
    parser.add_argument(
        "--baseline-size",
        type=_non_negative_int,
        default=DEFAULT_BASELINE_SIZE,
        help=(
            "Number of PubMed abstracts to fetch when building the baseline "
            f"(default: {DEFAULT_BASELINE_SIZE})"
        ),
    )
    parser.add_argument(
        "--baseline-pdat-range",
        type=str,
        default=None,
        help=(
            "Publication-date range for baseline sampling, 'YYYY:YYYY' "
            "(default: '2020:<current year>')"
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of a human-readable table",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write output to this file instead of stdout",
    )
    return parser.parse_args(argv)


def _resolve_phrase_top(args: argparse.Namespace) -> int:
    if args.query_top is not None:
        warnings.warn(
            "--query-top is deprecated; use --phrase-top",
            DeprecationWarning,
            stacklevel=2,
        )
        return int(args.query_top)
    return int(args.phrase_top)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    args = _parse_args(argv)
    from dotenv import load_dotenv  # python-dotenv pinned in requirements.txt

    load_dotenv()

    if args.build_baseline:
        pdat_range = args.baseline_pdat_range or f"2020:{_dt.date.today().year}"
        try:
            build_baseline_cache(
                size=args.baseline_size,
                output_path=args.baseline_cache,
                pdat_range=pdat_range,
            )
        except (RuntimeError, ValueError) as e:
            logger.error(str(e))
            return 1
        return 0

    try:
        papers = load_corpus(args.xml_dir)
    except FileNotFoundError as e:
        logger.error(str(e))
        return 1

    if not papers:
        logger.error("No parseable papers found.")
        return 1

    if not args.no_fulltext:
        pmids_for_fulltext = [p.pmid for p in papers if p.pmid]
        if pmids_for_fulltext:
            try:
                fulltext_records = fetch_fulltext_batch(
                    pmids_for_fulltext,
                    args.fulltext_cache,
                    email=os.getenv("ENTREZ_EMAIL"),
                    api_key=os.getenv("NCBI_API_KEY") or os.getenv("ENTREZ_KEY"),
                )
            except RuntimeError as e:
                logger.warning(
                    f"Full-text harvest disabled — {e}. "
                    f"Run with --no-fulltext to silence."
                )
                fulltext_records = {}
            for paper in papers:
                if paper.pmid and (record := fulltext_records.get(paper.pmid)):
                    paper.fulltext = record.as_text()
            with_text = sum(
                1 for r in fulltext_records.values() if r.pmcid is not None
            )
            logger.info(
                f"Full-text enrichment: {with_text}/{len(pmids_for_fulltext)} "
                f"papers have PMC versions; "
                f"{len(pmids_for_fulltext) - with_text} contribute "
                f"title+abstract only."
            )

    try:
        baseline = load_baseline_cache(args.baseline_cache)
    except FileNotFoundError as e:
        logger.error(str(e))
        return 1
    except RuntimeError as e:
        logger.error(str(e))
        return 1

    mesh_map: dict[str, list[MeshDescriptor]] | None = None
    if not args.no_mesh:
        pmids = [p.pmid for p in papers if p.pmid]
        if pmids:
            try:
                mesh_map = fetch_mesh_terms(pmids, args.mesh_cache)
            except RuntimeError as e:
                logger.warning(
                    f"MeSH harvest disabled — {e}. Run with --no-mesh to silence."
                )

    phrase_top = _resolve_phrase_top(args)

    result = distill_keywords(
        papers,
        baseline=baseline,
        top_n=args.top_n,
        min_df=args.min_df,
        min_llr=args.min_llr,
        mesh_descriptors=mesh_map,
        mesh_top=args.mesh_top,
        phrase_top=phrase_top,
    )

    if args.json:
        payload = to_json(result)
        if args.output:
            try:
                args.output.write_text(payload, encoding="utf-8")
            except OSError as e:
                logger.error(f"Could not write to {args.output}: {e}")
                return 1
            logger.info(f"Wrote JSON output to {args.output}")
        else:
            print(payload)
        return 0

    if args.output:
        try:
            with args.output.open("w", encoding="utf-8") as f:
                write_text_report(
                    result,
                    mesh_top=args.mesh_top,
                    phrase_top=phrase_top,
                    query_format=args.query_format,
                    stream=f,
                )
        except OSError as e:
            logger.error(f"Could not write to {args.output}: {e}")
            return 1
        logger.info(f"Wrote text report to {args.output}")
    else:
        write_text_report(
            result,
            mesh_top=args.mesh_top,
            phrase_top=phrase_top,
            query_format=args.query_format,
            stream=sys.stdout,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
