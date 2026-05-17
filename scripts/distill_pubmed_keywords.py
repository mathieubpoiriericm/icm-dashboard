"""Distill PubMed-API-ready keywords from a curated MODS bibliography.

Reads MODS XML files in the primary directory (default
``data/bibentry/xml/``). Tokenises titles + abstracts, then ranks
unigrams, bigrams, trigrams and ALL-CAPS acronyms by **Dunning's
log-likelihood ratio** of
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

*Methodological note*: the foreground and cached PubMed baseline both
use title+abstract text. The foreground still applies stopword and
content filtering at counting time while the baseline does not, so
``DEFAULT_MIN_LLR`` remains a pragmatic ranking cutoff rather than a
strict significance test.

The baseline cache must be built once before first ranking run; runs
are offline thereafter and the cache should be refreshed roughly
yearly:

    python scripts/distill_pubmed_keywords.py --build-baseline

Usage:
    python scripts/distill_pubmed_keywords.py --build-baseline
    python scripts/distill_pubmed_keywords.py
    python scripts/distill_pubmed_keywords.py --xml-dir data/bibentry/xml
    python scripts/distill_pubmed_keywords.py --no-mesh
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
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, TextIO

from lxml import etree  # type: ignore[import-untyped]
from rich import box
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.text import Text

logger = logging.getLogger(__name__)

# Stdout console for the interactive human-readable report. Non-TTY
# stdout uses ``write_text_report`` instead so redirects/pipes receive a
# simple, box-free text format. ``highlight`` is off so numbers/strings
# in our table cells aren't auto-restyled — we control all cell styling
# explicitly.
_console: Final[Console] = Console(stderr=False, highlight=False)

# Shared stderr console for both RichHandler and rich.Progress. Both
# must write through the *same* Console instance so the Live renderer
# can suspend the progress bar before a log line lands — otherwise
# warnings emitted during fetch loops stomp the active bar.
_stderr_console: Final[Console] = Console(stderr=True)

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

DEFAULT_TOP_N: Final[int] = 30
DEFAULT_MIN_DF: Final[int] = 2
DEFAULT_PHRASE_TOP: Final[int] = 10
DEFAULT_MESH_TOP: Final[int] = 10

DEFAULT_BASELINE_SIZE: Final[int] = 10_000
DEFAULT_BASELINE_BATCH: Final[int] = 200
DEFAULT_MESH_BATCH: Final[int] = 50
BASELINE_STALE_DAYS: Final[int] = 365
BASELINE_SCHEMA_VERSION: Final[int] = 1
_BASELINE_REBUILD_HINT: Final[str] = "Rebuild with --build-baseline."
# Drop n-grams with baseline count < this from the cache file to keep
# it under ~50MB. Side effect: LLR for cSVD n-grams that happen to occur
# exactly once in baseline treats their baseline count as 0 (smoothed
# to 0.5), slightly over-estimating distinctiveness for those terms.
# Unigrams and acronyms are stored without filtering.
BASELINE_NGRAM_MIN_COUNT: Final[int] = 2

DEFAULT_MIN_LLR: Final[float] = 6.63
# Heuristic cutoff (originally chosen because chi-square p<0.01 at df=1
# corresponds to a test statistic of 6.63). Baseline and foreground apply
# different content filters, so the strict chi-square interpretation is
# still approximate. See the module docstring's methodological note.

# Query-format choices. ``_QUERY_FORMAT_ALL`` is the CLI default and means
# "emit every variant"; the others map 1:1 to keys in the
# ``query_variants`` dict returned by ``build_query_variants``.
_QUERY_FORMAT_ALL: Final[str] = "all"
_QUERY_FORMAT_STRUCTURED: Final[str] = "structured"
_QUERY_FORMAT_MESH: Final[str] = "mesh"
_QUERY_FORMAT_TITLEABSTRACT: Final[str] = "titleabstract"
# ``hybrid`` mirrors the production ``SVD_QUERY`` shape:
# ``"<anchor>"[T/A] AND ("topic"[T/A] OR ...)``. Anchoring on a
# corpus-defining phrase (typically the 4-gram that the n-gram extractor
# can't surface, e.g. "cerebral small vessel disease") gives recall parity
# with the production query while filtering off-topic noise that broad
# MeSH headings or unanchored T/A pools pull in.
_QUERY_FORMAT_HYBRID: Final[str] = "hybrid"
_QUERY_FORMAT_VARIANTS: Final[tuple[str, ...]] = (
    _QUERY_FORMAT_STRUCTURED,
    _QUERY_FORMAT_MESH,
    _QUERY_FORMAT_TITLEABSTRACT,
    _QUERY_FORMAT_HYBRID,
)
_QUERY_FORMATS: Final[tuple[str, ...]] = (
    _QUERY_FORMAT_ALL,
    *_QUERY_FORMAT_VARIANTS,
)

# Project palette echoes www/custom.css so the CLI feels visually
# paired with the dashboard. Primary is a lightened indigo (the
# dashboard's #281E78 is unreadable on dark terminals).
_PRIMARY_COLOR: Final[str] = "#8B80E8"
_ACCENT_COLOR: Final[str] = "#FA4616"

# Single-pass tokenizer for the PubMed Boolean query renderer. Order of
# alternation matters: paren > op > tag > phrase > bare. The bare class
# is the fallback for anything that isn't whitespace, paren, bracket,
# or quote.
_QUERY_TOKEN_RE: Final[re.Pattern[str]] = re.compile(
    r"(?P<paren>[()])"
    r"|(?P<op>\bAND\b|\bOR\b|\bNOT\b)"
    r"|(?P<tag>\[[^\]]+\])"
    r'|(?P<phrase>"[^"]*")'
    r'|(?P<bare>[^\s()\[\]"]+)'
)
_QUERY_TOKEN_STYLES: Final[dict[str, str]] = {
    "paren": "dim",
    "op": "bold magenta",
    "tag": "cyan",
    "phrase": "bold yellow",
    "bare": "",
}

MIN_TOKEN_LENGTH: Final[int] = 3
MIN_ACRONYM_LENGTH: Final[int] = 2
# Upper bound covers known cSVD-relevant acronyms (CADASIL, CARASIL = 7;
# CAMRQ4 etc. = 6); was 6 in v1 and silently dropped CADASIL.
MAX_ACRONYM_LENGTH: Final[int] = 8

# NCBI rate limits — 3 req/s without API key, 10 req/s with.
_NCBI_SLEEP_NO_KEY: Final[float] = 0.34
_NCBI_SLEEP_WITH_KEY: Final[float] = 0.11
# Waits *between* attempts (in seconds). Total attempts = len + 1, so
# (1.0, 2.0) means: try, sleep 1s, try, sleep 2s, try — 3 attempts total.
_NCBI_RETRY_BACKOFF: Final[tuple[float, ...]] = (1.0, 2.0)

# Letter-led tokens that may carry intra-word hyphens. Hyphens must be
# followed by alphanumerics, which keeps "follow-up" intact but stops
# stray trailing hyphens leaking through.
_TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*"
)
_NUMERIC_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[\d\-]+$")
_PDAT_RANGE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^\d{4}:\d{4}$")
# PubMed IDs are numeric. We enforce this at cache-write sites because
# the PMID is interpolated into a filename — an untrusted value with
# slashes or "../" segments would let a tampered MODS record write
# outside the cache directory.
_PMID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^\d{1,12}$")

_WHITESPACE_RE: Final[re.Pattern[str]] = re.compile(r"\s+")


def _collapse_whitespace(s: str) -> str:
    """Collapse runs of whitespace to single spaces and strip the result."""
    return _WHITESPACE_RE.sub(" ", s).strip()


def _has_whitespace_or_empty(s: str) -> bool:
    """True when ``s`` is empty or contains any whitespace character."""
    return not s or any(c.isspace() for c in s)


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
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "if",
        "of",
        "at",
        "by",
        "for",
        "with",
        "about",
        "against",
        "between",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "to",
        "from",
        "up",
        "down",
        "in",
        "out",
        "on",
        "off",
        "over",
        "under",
        "than",
        "then",
        "once",
        "here",
        "there",
        "when",
        "where",
        "why",
        "how",
        "as",
        "because",
        "while",
        "although",
        "though",
        "since",
        "unless",
        "until",
        "whether",
        "via",
        "across",
        "within",
        "without",
        "among",
        "per",
        "upon",
        # Pronouns / demonstratives
        "me",
        "my",
        "we",
        "our",
        "us",
        "you",
        "your",
        "yours",
        "he",
        "him",
        "his",
        "she",
        "her",
        "hers",
        "it",
        "its",
        "they",
        "them",
        "their",
        "theirs",
        "what",
        "which",
        "who",
        "whom",
        "this",
        "that",
        "these",
        "those",
        # Aux / common verbs
        "am",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "having",
        "do",
        "does",
        "did",
        "doing",
        "would",
        "could",
        "should",
        "ought",
        "may",
        "might",
        "must",
        "can",
        "will",
        "shall",
        "let",
        # Structured-abstract section labels (appear as bare prefixes;
        # LLR helps but they still pollute trigrams).
        "background",
        "purpose",
        "objective",
        "objectives",
        "aim",
        "aims",
        "introduction",
        "discussion",
        "interpretation",
        "design",
        "findings",
        "funding",
        "setting",
        "interventions",
        "measurements",
        "outcomes",
        "outcome",
        "context",
        "methods",
        "results",
        "conclusions",
        "conclusion",
        # Full-text artifact labels
        "supplementary",
        "supplemental",
        "table",
        "figure",
        "fig",
        "appendix",
        # URL / data-availability boilerplate
        "doi",
        "org",
        "http",
        "https",
        "www",
        "pmid",
        "dryad",
        "figshare",
        "zenodo",
        # Number words
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "first",
        "second",
        "third",
        "fourth",
        "fifth",
    }
)

# Consulted before the suffix rules so they cannot mangle these forms
# (biomedical irregulars + singular-looking words ending in -s/-ies).
_IRREGULAR_PLURALS: Final[dict[str, str]] = {
    "analyses": "analysis",
    "biases": "bias",
    "classes": "class",
    "diagnoses": "diagnosis",
    "diabetes": "diabetes",
    "focuses": "focus",
    "prognoses": "prognosis",
    "processes": "process",
    "rabies": "rabies",
    "series": "series",
    "species": "species",
    "syntheses": "synthesis",
    "hypotheses": "hypothesis",
    "statuses": "status",
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
    "viruses": "virus",
}

# Suffixes that mean "this token does NOT end in a plural -s" — used to
# protect words like "nervous", "focus", "axis", "stress" from being
# stem-stripped to garbage.
_NO_STRIP_SUFFIXES: Final[tuple[str, ...]] = ("ous", "us", "is", "ss")

# Suffixes where the plural marker is "-es" (two chars), not "-s" — e.g.
# "processes", "boxes", "classes". Stripping a single "s" would leave
# "processe"/"boxe"; stripping two strips the whole "-es".
_ES_PLURAL_SUFFIXES: Final[tuple[str, ...]] = (
    "sses",
    "ches",
    "shes",
    "xes",
    "zes",
)

# Generic MeSH headings that describe the population or indexing frame
# rather than the paper's biomedical topic. If left in, high-frequency
# headings like "Humans" and "Female" crowd out useful query terms.
_MESH_STOP_TERMS: Final[frozenset[str]] = frozenset(
    {
        "Adolescent",
        "Adult",
        "Aged",
        "Aged, 80 and over",
        "Animals",
        "Child",
        "Child, Preschool",
        "Female",
        "Humans",
        "Infant",
        "Infant, Newborn",
        "Male",
        "Middle Aged",
        "Young Adult",
    }
)
_MESH_STOP_TERMS_CASEFOLD: Final[frozenset[str]] = frozenset(
    term.casefold() for term in _MESH_STOP_TERMS
)


# ---------------------------------------------------------------------------
# DATACLASSES
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PaperText:
    """Title + abstract text for one paper."""

    pmid: str | None
    title: str
    abstract: str

    @property
    def combined(self) -> str:
        return f"{self.title} {self.abstract}".strip()


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

    def __post_init__(self) -> None:
        object.__setattr__(self, "term", _collapse_whitespace(self.term))


@dataclass(slots=True, frozen=True)
class MeshDescriptor:
    term: str
    ui: str
    major: bool
    qualifiers: tuple[MeshQualifier, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "term", _collapse_whitespace(self.term))


@dataclass(slots=True)
class BaselineCounts:
    """Frozen PubMed background frequencies used as the LLR reference.

    Unigram/bigram/trigram keys are uniformly ``tuple[str, ...]`` so
    they line up with the foreground ranking keys; acronyms stay flat
    strings because the foreground detector treats acronyms as
    surface-form tokens (no stemming).
    """

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


@dataclass(slots=True, frozen=True)
class RankingInputs:
    """Per-n-gram foreground counts and optional baseline reference.

    Bundles the five values ``_rank_terms`` needs to score a single
    n-gram size (or the acronym slot) so the caller can build one record
    instead of threading five positional arguments through the signature.
    ``bg_counts`` / ``total_bg`` are ``None`` when no baseline is
    available — ``_rank_terms`` then falls back to DF ranking.
    """

    fg_counts: Counter[Any]
    fg_doc_freq: Counter[Any]
    total_fg: int
    bg_counts: Counter[Any] | None = None
    total_bg: int | None = None


# ---------------------------------------------------------------------------
# I/O HELPERS
# ---------------------------------------------------------------------------


def _is_valid_pmid(pmid: str | None) -> bool:
    """Return True iff ``pmid`` is a numeric string safe to use as a filename.

    PMIDs reach the cache layer from MODS XML and from NCBI responses;
    only the former is potentially attacker-controlled. Rejecting anything
    that doesn't match ``\\d{1,12}`` prevents a tampered MODS record from
    writing outside the cache directory via ``../`` segments.
    """
    return pmid is not None and _PMID_PATTERN.fullmatch(pmid) is not None


def _atomic_write(path: Path, writer: Callable[[Path], None]) -> None:
    """Atomically replace ``path`` with content produced by ``writer``.

    Creates a uniquely-named tmp file in the same directory, hands its
    path to ``writer`` (which opens it via ``open`` / ``gzip.open`` /
    etc.), then renames over the destination. If ``writer`` raises, the
    tmp file is unlinked and the exception re-raised — so a crashed
    write can't leave a half-written file the next read would reject.
    """
    fd, tmp_name = tempfile.mkstemp(
        prefix=f"{path.name}.", suffix=".tmp", dir=path.parent
    )
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        writer(tmp_path)
        tmp_path.replace(path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise


def _atomic_write_json(path: Path, payload: Any) -> None:
    """Write JSON to ``path`` atomically (see ``_atomic_write``)."""

    def _write(tmp: Path) -> None:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)

    _atomic_write(path, _write)


def _unique_valid_pmids(pmids: Iterable[str]) -> list[str]:
    """Return numeric PMIDs in first-seen order, dropping duplicates.

    Cache and network functions are keyed by PMID, so duplicate inputs only
    create redundant requests and can make progress callbacks report impossible
    totals. Invalid PMIDs are skipped here for defense in depth; direct callers
    can bypass ``parse_mods_file``.
    """
    cleaned = (str(p).strip() for p in pmids)
    valid: list[str] = []
    for pmid in cleaned:
        if not pmid:
            continue
        if not _is_valid_pmid(pmid):
            logger.warning(f"Refusing to use non-numeric PMID {pmid!r}")
            continue
        valid.append(pmid)
    return list(dict.fromkeys(valid))


def _local_name(elem: etree._Element) -> str:
    """Return an element's local tag name without its namespace."""
    return elem.tag.rsplit("}", 1)[-1] if isinstance(elem.tag, str) else ""


def _direct_children_named(elem: etree._Element, name: str) -> list[etree._Element]:
    """Return direct child elements whose local tag name matches ``name``."""
    return [child for child in elem if _local_name(child) == name]


def _normalized_identifier_type(identifier_type: str | None) -> str:
    """Return a compact identifier type key for tolerant MODS matching."""
    return re.sub(r"[^a-z0-9]+", "", (identifier_type or "").casefold())


def _coerce_to_bytes(raw: Any, context: str) -> bytes | None:
    """Coerce an Entrez reader payload to ``bytes`` for XML parsing.

    ``Bio.Entrez`` reader callbacks return ``bytes`` for most XML
    responses but may yield ``str`` or ``bytearray`` depending on the
    handle, and ``_ncbi_retry`` can return ``None`` on persistent
    failure (with its own retry-exhaustion log). Returns ``None`` for
    ``None`` input silently — the retry layer already logged. Returns
    ``None`` with a warning when the type is genuinely unrecognized.
    Empty ``bytes`` pass through so callers can attach a
    domain-specific "empty response" message.
    """
    if raw is None:
        return None
    if isinstance(raw, bytes):
        return raw
    if isinstance(raw, str):
        return raw.encode("utf-8")
    if isinstance(raw, bytearray):
        return bytes(raw)
    logger.warning(f"{context} returned {type(raw).__name__}, expected bytes")
    return None


# ---------------------------------------------------------------------------
# XML PARSING (MODS)
# ---------------------------------------------------------------------------


def _element_text(elem: etree._Element | None) -> str:
    """Concatenate all text within an element (handles mixed content)."""
    if elem is None:
        return ""
    return _collapse_whitespace(
        "".join(t for t in elem.itertext() if isinstance(t, str))
    )


def _select_primary_title_info(mods_el: etree._Element) -> etree._Element | None:
    """Return the best article-level ``titleInfo`` element.

    MODS allows alternate, translated, and abbreviated title blocks. The
    untyped titleInfo is the primary title in normal article records, so
    prefer a populated untyped block when present. If the primary block is
    empty, fall back to another populated block rather than losing the title
    entirely.
    """
    candidates = _direct_children_named(mods_el, "titleInfo")
    if not candidates:
        return None

    populated = [
        candidate
        for candidate in candidates
        if _element_text(candidate.find(f"./{_NS}title"))
        or _element_text(candidate.find(f"./{_NS}subTitle"))
    ]
    search_space = populated or candidates
    for candidate in search_space:
        if not (candidate.get("type") or "").strip():
            return candidate
    return search_space[0]


def _pmid_identifier_elements(mods_el: etree._Element) -> list[etree._Element]:
    """Return direct MODS identifier elements that appear to carry a PMID."""
    pmid_type_keys = {"pmid", "pubmed", "pubmedid"}
    matches: list[etree._Element] = []
    for identifier in _direct_children_named(mods_el, "identifier"):
        if _normalized_identifier_type(identifier.get("type")) in pmid_type_keys:
            matches.append(identifier)
    return matches


def _mods_has_title_or_abstract(mods_el: etree._Element) -> bool:
    """Return True when a MODS element has article text worth parsing."""
    title_info = _select_primary_title_info(mods_el)
    if title_info is not None:
        title_el = title_info.find(f"./{_NS}title")
        subtitle_el = title_info.find(f"./{_NS}subTitle")
        if _element_text(title_el) or _element_text(subtitle_el):
            return True
    return any(_element_text(el) for el in mods_el.findall(f"./{_NS}abstract"))


def _mods_has_article_signal(mods_el: etree._Element) -> bool:
    """Return True for MODS records that look like article-level records."""
    if any(_element_text(el) for el in mods_el.findall(f"./{_NS}abstract")):
        return True
    return any(
        _is_valid_pmid(_element_text(el)) for el in _pmid_identifier_elements(mods_el)
    )


def _select_mods_element(root: etree._Element) -> etree._Element:
    """Return the best article-level MODS element beneath ``root``."""
    if _local_name(root) == "mods":
        # ``Element.find(".//mods")`` does not include the element itself.
        # Prefer the root here so nested related-item MODS blocks cannot
        # steal the article title when the collection wrapper is absent.
        return root

    # For <modsCollection>, prefer a direct child before falling back to a
    # deeper wrapper search. If an exporter leaves a leading empty direct
    # <mods/> block, use the first direct child that actually has article text
    # instead of dropping the file.
    direct_mods = _direct_children_named(root, "mods")
    for candidate in direct_mods:
        if _mods_has_title_or_abstract(candidate) and _mods_has_article_signal(
            candidate
        ):
            return candidate
    for candidate in direct_mods:
        if _mods_has_title_or_abstract(candidate):
            return candidate

    direct_mod_ids = {id(el) for el in direct_mods}
    descendant_mods = root.findall(f".//{_NS}mods")
    for candidate in descendant_mods:
        if id(candidate) in direct_mod_ids:
            continue
        if _mods_has_title_or_abstract(candidate) and _mods_has_article_signal(
            candidate
        ):
            return candidate
    for candidate in descendant_mods:
        if id(candidate) in direct_mod_ids:
            continue
        if _mods_has_title_or_abstract(candidate):
            return candidate
    if direct_mods:
        return direct_mods[0]
    if descendant_mods:
        return descendant_mods[0]
    return root  # tolerate namespace-stripped/partial records


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
    mods_el = _select_mods_element(root)

    title_info = _select_primary_title_info(mods_el)
    title_el = title_info.find(f"./{_NS}title") if title_info is not None else None
    subtitle_el = (
        title_info.find(f"./{_NS}subTitle") if title_info is not None else None
    )
    title_parts = [
        s for s in (_element_text(title_el), _element_text(subtitle_el)) if s
    ]
    title = ": ".join(title_parts)
    abstract = " ".join(
        part
        for part in (_element_text(el) for el in mods_el.findall(f"./{_NS}abstract"))
        if part
    )

    if not title and not abstract:
        logger.debug(f"No title or abstract in {path.name}")
        return None

    pmid_text = None
    for pmid_el in _pmid_identifier_elements(mods_el):
        candidate = _element_text(pmid_el)
        if not candidate:
            continue
        if _is_valid_pmid(candidate):
            pmid_text = candidate
            break
        # Reject malformed PMIDs at the corpus boundary — they would
        # later be interpolated into cache filenames.
        logger.warning(f"Ignoring non-numeric PMID {candidate!r} in {path.name}")
    if pmid_text is None and _is_valid_pmid(path.stem):
        # MODS converted from BibTeX often lack an inner PubMed identifier;
        # users compensate by naming the file after the PMID.
        pmid_text = path.stem
        logger.info(
            f"Using filename PMID {pmid_text} for {path.name}"
            " (no PubMed identifier in MODS)"
        )

    return PaperText(
        pmid=pmid_text,
        title=title,
        abstract=abstract,
    )


def load_corpus(
    xml_dir: Path,
    *,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[PaperText]:
    """Parse every ``*.xml`` file in ``xml_dir`` into PaperText records.

    Files are processed in sorted order. Raises ``FileNotFoundError`` if
    the directory is missing.
    """
    if not xml_dir.is_dir():
        raise FileNotFoundError(f"XML directory not found: {xml_dir}")

    files = sorted(xml_dir.glob("*.xml"))
    total = len(files)
    papers: list[PaperText] = []
    for i, f in enumerate(files, start=1):
        parsed = parse_mods_file(f)
        if parsed is not None:
            papers.append(parsed)
        if progress_callback is not None:
            progress_callback(i, total)
    logger.info(f"Parsed {len(papers)} paper(s) from {total} XML file(s) in {xml_dir}")
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
    if lower.endswith("ies") and len(lower) > 4 and lower[-4] not in "aeiou":
        return lower[:-3] + "y"
    if lower.endswith(_ES_PLURAL_SUFFIXES):
        stripped = lower[:-2]
        if len(stripped) >= MIN_TOKEN_LENGTH:
            return stripped
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


def _validate_ranking_counts(
    counts: Mapping[Any, int],
    total: int,
    *,
    label: str,
    total_label: str,
) -> None:
    """Reject negative totals or counts whose positive sum exceeds the total.

    Non-positive per-term counts are skipped by the ranking loops, so the
    invariant only needs to hold for the positive contributions.
    """
    if total < 0:
        raise ValueError(f"{total_label} must be non-negative, got {total}")
    total_positive = sum(v for v in counts.values() if v > 0)
    if total_positive > total:
        raise ValueError(
            f"Sum of {label} counts exceeds {total_label} "
            f"({total_positive} > {total})"
        )


def _rank_terms(
    inputs: RankingInputs,
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
    if not math.isfinite(min_llr) or min_llr < 0:
        raise ValueError(f"min_llr must be a finite non-negative number, got {min_llr}")
    if min_df < 0:
        raise ValueError(f"min_df must be non-negative, got {min_df}")

    if inputs.bg_counts is None or inputs.total_bg is None:
        return _rank_terms_df(inputs, min_df=min_df, top_n=top_n, display=display)

    _validate_ranking_counts(
        inputs.fg_counts, inputs.total_fg, label="foreground", total_label="total_fg"
    )
    _validate_ranking_counts(
        inputs.bg_counts, inputs.total_bg, label="baseline", total_label="total_bg"
    )

    if top_n <= 0 or inputs.total_fg <= 0:
        return []
    if inputs.total_bg == 0:
        return _rank_terms_df(inputs, min_df=min_df, top_n=top_n, display=display)

    total_fg = inputs.total_fg
    total_bg = inputs.total_bg
    scored: list[KeywordScore] = []
    for key, fg in inputs.fg_counts.items():
        if fg <= 0:
            continue
        if inputs.fg_doc_freq[key] < min_df:
            continue
        bg = inputs.bg_counts.get(key, 0)
        if bg < 0:
            raise ValueError(f"Baseline count for {key!r} is negative ({bg})")
        if (fg / total_fg) <= (bg / total_bg):
            continue
        llr = _llr_score(fg, bg, total_fg - fg, total_bg - bg)
        if llr < min_llr:
            continue
        term = (
            display[key] if display is not None and key in display else _stringify(key)
        )
        scored.append(
            KeywordScore(
                term=term,
                document_frequency=inputs.fg_doc_freq[key],
                total_count=fg,
                llr=llr,
            )
        )

    scored.sort(key=lambda k: (-k.llr, -k.document_frequency, k.term))
    return scored[:top_n]


def _rank_terms_df(
    inputs: RankingInputs,
    *,
    min_df: int,
    top_n: int,
    display: Mapping[Any, str] | None = None,
) -> list[KeywordScore]:
    """Fallback ranking by document frequency — used when no baseline."""
    if min_df < 0:
        raise ValueError(f"min_df must be non-negative, got {min_df}")
    _validate_ranking_counts(
        inputs.fg_counts, inputs.total_fg, label="foreground", total_label="total_fg"
    )
    if top_n <= 0:
        return []

    scored: list[KeywordScore] = []
    for key, fg in inputs.fg_counts.items():
        if fg <= 0:
            continue
        if inputs.fg_doc_freq[key] < min_df:
            continue
        term = (
            display[key] if display is not None and key in display else _stringify(key)
        )
        scored.append(
            KeywordScore(
                term=term,
                document_frequency=inputs.fg_doc_freq[key],
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


def _ncbi_retry(
    fn: Callable[..., Any],
    *args: Any,
    _reader: Callable[[Any], Any],
    **kwargs: Any,
) -> Any:
    """Open a handle via ``fn`` and read it via ``_reader`` with retries.

    The handle is closed between attempts so transient mid-transfer
    errors get a fresh connection. Both the open and the read run
    inside the retry boundary.
    """
    last_exc: Exception | None = None
    total = len(_NCBI_RETRY_BACKOFF) + 1
    for attempt in range(total):
        try:
            handle = fn(*args, **kwargs)
            try:
                return _reader(handle)
            finally:
                with contextlib.suppress(Exception):
                    handle.close()
        except Exception as exc:  # noqa: BLE001 — Entrez raises various I/O types
            last_exc = exc
            if attempt == total - 1:
                break
            wait = _NCBI_RETRY_BACKOFF[attempt]
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
    progress_callback: Callable[[int, int], None] | None = None,
) -> Path:
    """Fetch a random-ish PubMed sample and write the frequency cache.

    Uses ``Bio.Entrez.esearch`` with a broad English/journal-article
    query in the given PDAT range, sorted by recency, taking the first
    ``size`` PMIDs. Then ``efetch`` in batches and accumulates n-gram +
    acronym counts. Writes gzipped JSON to ``output_path``.
    """
    if size <= 0:
        raise ValueError(f"size must be positive, got {size}")
    if batch_size <= 0:
        raise ValueError(f"batch_size must be positive, got {batch_size}")

    if not _PDAT_RANGE_PATTERN.match(pdat_range):
        raise ValueError(
            f"pdat_range must be 'YYYY:YYYY' (e.g. '2020:2024'), got {pdat_range!r}"
        )
    pdat_from, pdat_to = pdat_range.split(":", 1)
    if int(pdat_from) > int(pdat_to):
        raise ValueError(
            f"pdat_range start year must be <= end year, got {pdat_range!r}"
        )
    from Bio import Entrez

    resolved_key = _configure_entrez(email=email, api_key=api_key)
    query = (
        f'"english"[Language] AND "journal article"[Publication Type] '
        f'AND ("{pdat_from}"[PDAT] : "{pdat_to}"[PDAT])'
    )
    logger.info(f"Fetching baseline ({size} abstracts, PDAT={pdat_range})...")

    results = _ncbi_retry(
        Entrez.esearch,
        db="pubmed",
        term=query,
        retmax=size,
        sort="date",
        usehistory="n",
        _reader=Entrez.read,
    )
    _ncbi_sleep(resolved_key)

    if not isinstance(results, Mapping):
        raise RuntimeError(
            "PubMed esearch returned a malformed baseline response "
            f"({type(results).__name__}); expected a mapping."
        )
    raw_pmids = results.get("IdList", [])
    if isinstance(raw_pmids, str | bytes) or not isinstance(raw_pmids, Iterable):
        raise RuntimeError("PubMed esearch baseline response has a malformed IdList.")
    pmids = _unique_valid_pmids(str(pmid) for pmid in raw_pmids)
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
        completed = min(start + batch_size, len(pmids))
        try:
            raw = _ncbi_retry(
                Entrez.efetch,
                db="pubmed",
                id=",".join(batch),
                rettype="abstract",
                retmode="xml",
                _reader=lambda h: h.read(),
            )
            _ncbi_sleep(resolved_key)

            raw = _coerce_to_bytes(raw, f"Baseline batch {start}-{start + len(batch)}")
            if raw is None:
                continue
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
        finally:
            # Tick progress even on parse failure so the bar doesn't
            # appear stuck for the duration of a malformed batch.
            if progress_callback is not None:
                progress_callback(completed, len(pmids))
            elif (start // batch_size) % 5 == 4:
                # Fallback when no live progress is wired up — keep the
                # legacy info-log cadence so headless runs still surface
                # heartbeat output.
                logger.info(f"  baseline progress: {completed}/{len(pmids)} PMIDs")

    logger.info(f"Baseline assembled from {total_docs} parseable abstract(s).")
    if total_docs == 0:
        raise RuntimeError(
            "PubMed baseline fetch produced no parseable abstracts; "
            "not writing an empty baseline cache."
        )

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

    def _write_gzipped(tmp: Path) -> None:
        with gzip.open(tmp, "wt", encoding="utf-8") as gz:
            json.dump(payload, gz)

    _atomic_write(output_path, _write_gzipped)
    logger.info(f"Wrote baseline cache to {output_path}")
    return output_path


_REQUIRED_BASELINE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "built_at",
        "params",
        "total_docs",
        "total_unigrams",
        "total_bigrams",
        "total_trigrams",
        "total_acronyms",
        "unigrams",
        "bigrams",
        "trigrams",
        "acronyms",
    }
)


def _baseline_error(detail: str) -> RuntimeError:
    return RuntimeError(f"{detail} {_BASELINE_REBUILD_HINT}")


def _coerce_baseline_count(name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _baseline_error(
            f"Baseline cache field {name!r} contains a non-integer count."
        )
    if value < 0:
        raise _baseline_error(f"Baseline cache field {name!r} has negative counts.")
    return value


def _load_baseline_counter(
    payload: Mapping[str, Any],
    name: str,
    key_mapper: Callable[[str], Any],
    *,
    expected_arity: int | None = None,
) -> Counter[Any]:
    raw = payload.get(name, {})
    if not isinstance(raw, Mapping):
        raise _baseline_error(f"Baseline cache field {name!r} is malformed.")
    values: dict[Any, int] = {}
    try:
        for k, v in raw.items():
            mapped = key_mapper(str(k))
            if isinstance(mapped, tuple):
                if expected_arity is not None and len(mapped) != expected_arity:
                    raise ValueError(
                        f"expected {expected_arity} token(s), got {len(mapped)}"
                    )
                if any(_has_whitespace_or_empty(part) for part in mapped):
                    raise ValueError("empty/whitespace token in n-gram key")
            elif _has_whitespace_or_empty(str(mapped)):
                raise ValueError("empty/whitespace key")
            values[mapped] = _coerce_baseline_count(name, v)
    except (TypeError, ValueError) as e:
        raise _baseline_error(f"Baseline cache field {name!r} is malformed.") from e
    return Counter(values)


def load_baseline_cache(path: Path) -> BaselineCounts:
    """Read a baseline cache; raise if missing or schema-mismatched."""
    if not path.exists():
        raise FileNotFoundError(
            f"Baseline cache not found at {path}. Build it first with:\n"
            f"  python {Path(sys.argv[0]).name} --build-baseline"
        )
    try:
        with gzip.open(path, "rt", encoding="utf-8") as gz:
            payload = json.load(gz)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        raise _baseline_error(f"Could not read baseline cache at {path}: {e}.") from e
    if not isinstance(payload, Mapping):
        raise _baseline_error(f"Baseline cache at {path} is malformed.")

    version = payload.get("schema_version")
    if version != BASELINE_SCHEMA_VERSION:
        raise _baseline_error(
            f"Baseline cache schema {version} != expected {BASELINE_SCHEMA_VERSION}."
        )

    missing_fields = sorted(_REQUIRED_BASELINE_FIELDS.difference(payload))
    if missing_fields:
        joined = ", ".join(repr(f) for f in missing_fields)
        raise _baseline_error(f"Baseline cache is missing required field(s): {joined}.")

    built_at_value = payload["built_at"]
    if not isinstance(built_at_value, str) or not built_at_value.strip():
        raise _baseline_error("Baseline cache field 'built_at' is malformed.")
    try:
        built_at = _dt.datetime.fromisoformat(built_at_value.strip())
    except ValueError as e:
        raise _baseline_error("Baseline cache field 'built_at' is malformed.") from e
    if built_at.tzinfo is None:
        built_at = built_at.replace(tzinfo=_dt.UTC)
    now = _dt.datetime.now(_dt.UTC)
    if built_at - now > _dt.timedelta(days=1):
        raise _baseline_error("Baseline cache field 'built_at' is in the future.")
    age_days = max(0, (now - built_at).days)
    if age_days > BASELINE_STALE_DAYS:
        logger.warning(
            f"Baseline cache is {age_days} days old (> {BASELINE_STALE_DAYS}). "
            f"Consider rebuilding with --build-baseline."
        )

    if not isinstance(payload["params"], Mapping):
        raise _baseline_error("Baseline cache field 'params' is malformed.")

    total_docs = _coerce_baseline_count("total_docs", payload["total_docs"])
    total_unigrams = _coerce_baseline_count("total_unigrams", payload["total_unigrams"])
    total_bigrams = _coerce_baseline_count("total_bigrams", payload["total_bigrams"])
    total_trigrams = _coerce_baseline_count("total_trigrams", payload["total_trigrams"])
    total_acronyms = _coerce_baseline_count("total_acronyms", payload["total_acronyms"])
    # Cache stores n-gram keys flat as strings for compact JSON; ranking keys
    # them as (stem,) / (s1, s2) / (s1, s2, s3) tuples — wrap on load.
    unigrams = _load_baseline_counter(
        payload, "unigrams", lambda k: (k,), expected_arity=1
    )
    bigrams = _load_baseline_counter(
        payload, "bigrams", lambda k: tuple(k.split(" ")), expected_arity=2
    )
    trigrams = _load_baseline_counter(
        payload, "trigrams", lambda k: tuple(k.split(" ")), expected_arity=3
    )
    acronyms = _load_baseline_counter(payload, "acronyms", lambda k: k)

    if total_docs <= 0:
        raise _baseline_error("Baseline cache field 'total_docs' must be positive.")
    for name, total, counts in (
        ("total_unigrams", total_unigrams, unigrams),
        ("total_bigrams", total_bigrams, bigrams),
        ("total_trigrams", total_trigrams, trigrams),
        ("total_acronyms", total_acronyms, acronyms),
    ):
        if sum(counts.values()) > total:
            raise _baseline_error(
                f"Baseline cache field {name!r} is smaller than stored counts."
            )

    return BaselineCounts(
        total_docs=total_docs,
        unigrams=unigrams,
        bigrams=bigrams,
        trigrams=trigrams,
        acronyms=acronyms,
        total_unigrams=total_unigrams,
        total_bigrams=total_bigrams,
        total_trigrams=total_trigrams,
        total_acronyms=total_acronyms,
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
                {"term": q.term, "ui": q.ui, "major": q.major} for q in d.qualifiers
            ],
        }
        for d in items
    ]


def _cached_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be boolean")
    return value


def _cached_str(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    return value


def _descriptors_from_jsonable(items: Any) -> list[MeshDescriptor]:
    if not isinstance(items, list):
        raise ValueError("descriptors must be a list")

    descriptors: list[MeshDescriptor] = []
    for d in items:
        if not isinstance(d, Mapping):
            raise ValueError("descriptor entries must be objects")
        term = d.get("term")
        if not isinstance(term, str) or not term.strip():
            raise ValueError("descriptor term must be a non-empty string")
        qualifiers_raw = d.get("qualifiers", [])
        if not isinstance(qualifiers_raw, list):
            raise ValueError("descriptor qualifiers must be a list")

        qualifiers: list[MeshQualifier] = []
        for q in qualifiers_raw:
            if not isinstance(q, Mapping):
                raise ValueError("qualifier entries must be objects")
            q_term = q.get("term")
            if not isinstance(q_term, str) or not q_term.strip():
                raise ValueError("qualifier term must be a non-empty string")
            qualifiers.append(
                MeshQualifier(
                    term=q_term,
                    ui=_cached_str(q.get("ui", ""), "qualifier ui"),
                    major=_cached_bool(q.get("major", False), "qualifier major"),
                )
            )

        descriptors.append(
            MeshDescriptor(
                term=term,
                ui=_cached_str(d.get("ui", ""), "descriptor ui"),
                major=_cached_bool(d.get("major", False), "descriptor major"),
                qualifiers=tuple(qualifiers),
            )
        )
    return descriptors


def fetch_mesh_terms(
    pmids: Iterable[str],
    cache_dir: Path,
    *,
    email: str | None = None,
    api_key: str | None = None,
    batch_size: int = DEFAULT_MESH_BATCH,
    fetcher: Any = None,
    progress_callback: Callable[[int, int], None] | None = None,
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
    if batch_size <= 0:
        raise ValueError(f"batch_size must be positive, got {batch_size}")
    cache_dir.mkdir(parents=True, exist_ok=True)
    pmid_list = _unique_valid_pmids(pmids)
    out: dict[str, list[MeshDescriptor]] = {}
    missing: list[str] = []

    total = len(pmid_list)
    for pmid in pmid_list:
        cache_path = cache_dir / f"{pmid}.json"
        if cache_path.exists():
            try:
                with cache_path.open(encoding="utf-8") as f:
                    cached = json.load(f)
                if not isinstance(cached, Mapping):
                    raise ValueError("cache payload must be an object")
                cached_pmid = cached.get("pmid")
                if not isinstance(cached_pmid, str) or cached_pmid != pmid:
                    raise ValueError(
                        f"cache PMID {cached_pmid!r} does not match {pmid!r}"
                    )
                if "descriptors" not in cached:
                    raise ValueError("cache payload missing descriptors")
                out[pmid] = _descriptors_from_jsonable(cached["descriptors"])
            except (
                json.JSONDecodeError,
                OSError,
                AttributeError,
                TypeError,
                KeyError,
                ValueError,
            ) as e:
                # AttributeError/TypeError catch JSON that parses but isn't
                # a dict; KeyError catches descriptors missing the required
                # ``term`` field.
                logger.warning(f"Corrupt MeSH cache for PMID {pmid}: {e}; will refetch")
                missing.append(pmid)
        else:
            missing.append(pmid)

    if progress_callback is not None:
        # Report cache-hit count up front so the bar jumps to the
        # already-resolved fraction before any network work begins.
        progress_callback(total - len(missing), total)

    if not missing:
        return out

    if fetcher is None:
        try:
            resolved_key = _configure_entrez(email=email, api_key=api_key)
            from Bio import Entrez
        except (ImportError, RuntimeError) as e:
            logger.warning(
                f"MeSH fetch unavailable: {e}; using cached descriptors only"
            )
            if progress_callback is not None:
                progress_callback(total, total)
            return out

        def _default_fetcher(batch: list[str]) -> bytes:
            raw = _ncbi_retry(
                Entrez.efetch,
                db="pubmed",
                id=",".join(batch),
                rettype="medline",
                retmode="xml",
                _reader=lambda h: h.read(),
            )
            _ncbi_sleep(resolved_key)
            return raw

        fetcher = _default_fetcher

    cached_count = total - len(missing)
    for start in range(0, len(missing), batch_size):
        batch = missing[start : start + batch_size]
        try:
            try:
                raw = fetcher(batch)
            except Exception as e:  # noqa: BLE001 — surface any fetch failure
                logger.warning(
                    f"MeSH efetch batch {start}-{start + len(batch)} failed: {e}"
                )
                continue
            raw = _coerce_to_bytes(
                raw, f"MeSH efetch batch {start}-{start + len(batch)}"
            )
            if raw is None:
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
                if pmid not in parsed:
                    # NCBI omitted this PMID from the response (truncation,
                    # partial response, ...). Don't cache anything — a cached
                    # empty would suppress retries indefinitely.
                    logger.warning(
                        f"PubMed response did not include PMID {pmid}; "
                        f"will retry next run"
                    )
                    continue
                descriptors = parsed[pmid]
                out[pmid] = descriptors
                # Defense in depth: the PMID came from the request batch,
                # which originated in MODS XML. parse_mods_file already
                # rejects non-numeric PMIDs, but the cache layer must not
                # rely on that one upstream check.
                if not _is_valid_pmid(pmid):
                    logger.warning(
                        f"Refusing to write MeSH cache for non-numeric PMID {pmid!r}"
                    )
                    continue
                cache_path = cache_dir / f"{pmid}.json"
                try:
                    _atomic_write_json(
                        cache_path,
                        {
                            "pmid": pmid,
                            "descriptors": _descriptors_to_jsonable(descriptors),
                            "fetched_at": _dt.datetime.now(_dt.UTC).isoformat(),
                        },
                    )
                except OSError as e:
                    logger.warning(f"Could not write MeSH cache for PMID {pmid}: {e}")
        finally:
            # Tick progress even when a batch fails wholesale (fetcher
            # exception, parse error) — otherwise the bar appears stuck
            # for the duration of every failed batch.
            if progress_callback is not None:
                progress_callback(cached_count + start + len(batch), total)

    return out


def aggregate_mesh(
    pmid_to_descriptors: Mapping[str, list[MeshDescriptor]],
    *,
    top_n: int,
) -> list[KeywordScore]:
    """Rank MeSH descriptors by DF across papers; weight major topics 2x.

    Duplicate descriptors in a single paper's heading list contribute one
    weight unit per paper (the highest weight wins on conflict — Major
    trumps Minor). This keeps weight a per-paper signal consistent with DF.
    """
    if top_n <= 0:
        return []

    doc_freq: Counter[str] = Counter()
    weight: Counter[str] = Counter()
    surface_counts: dict[str, Counter[str]] = {}
    for descriptors in pmid_to_descriptors.values():
        per_paper_weight: dict[str, int] = {}
        per_paper_surface: dict[str, str] = {}
        for d in descriptors:
            if not d.term or d.term.casefold() in _MESH_STOP_TERMS_CASEFOLD:
                continue
            key = d.term.casefold()
            w = 2 if d.major else 1
            if w > per_paper_weight.get(key, 0):
                per_paper_weight[key] = w
                per_paper_surface[key] = d.term
            elif key not in per_paper_surface:
                per_paper_surface[key] = d.term
        for key, w in per_paper_weight.items():
            weight[key] += w
            doc_freq[key] += 1
            surface_counts.setdefault(key, Counter())[per_paper_surface[key]] += 1
    scored = [
        KeywordScore(
            term=surface_counts[key].most_common(1)[0][0],
            document_frequency=doc_freq[key],
            total_count=weight[key],
        )
        for key in doc_freq
    ]
    scored.sort(key=lambda k: (-k.document_frequency, -k.total_count, k.term))
    return scored[:top_n]


# ---------------------------------------------------------------------------
# DISTILLATION
# ---------------------------------------------------------------------------


def _foreground_stats_for(
    paper_stem_token_pairs: list[list[tuple[str, str]]],
    n: int,
    *,
    filter_content: bool,
) -> tuple[
    Counter[tuple[str, ...]],
    Counter[tuple[str, ...]],
    dict[tuple[str, ...], str],
]:
    """Compute (term-frequency, document-frequency, display map) for n-grams.

    Aggregates by stem so plural variants share a count; ``display`` maps
    each stem-key to its modal surface n-gram form so output reads as
    English, not stems.

    When ``filter_content`` is set, an n-gram is dropped when any token
    fails the stopword/length/numeric filter — applied to the foreground
    but never to the baseline (so LLR sees consistent denominators). Both
    stem and surface form are checked against ``_STOPWORDS``: stemming
    collapses plural-only section labels ("results"→"result") to a stem
    that isn't a stopword, so checking the surface too is what keeps the
    bare "Methods:"/"Results:" prefixes filtered. The same filter applies
    to surface-form aggregation, so filtered labels like "Results" cannot
    become the displayed form for a kept singular content token.
    """
    tf: Counter[tuple[str, ...]] = Counter()
    df: Counter[tuple[str, ...]] = Counter()
    surface_counts: dict[tuple[str, ...], Counter[tuple[str, ...]]] = {}
    for tokens in paper_stem_token_pairs:
        if len(tokens) < n:
            continue
        in_paper: set[tuple[str, ...]] = set()
        for i in range(len(tokens) - n + 1):
            pair_slice = tokens[i : i + n]
            if filter_content and not all(
                _is_content_token(s) and t not in _STOPWORDS for s, t in pair_slice
            ):
                continue
            stems = tuple(s for s, _ in pair_slice)
            surfaces = tuple(t for _, t in pair_slice)
            tf[stems] += 1
            in_paper.add(stems)
            surface_counts.setdefault(stems, Counter())[surfaces] += 1
        df.update(in_paper)
    display = {
        stems: " ".join(surfaces.most_common(1)[0][0])
        for stems, surfaces in surface_counts.items()
    }
    return tf, df, display


def _foreground_acronyms(
    paper_tokens: list[list[str]],
) -> tuple[Counter[str], Counter[str]]:
    """Detect ALL-CAPS short tokens — returns (TF, DF) across papers."""
    tf: Counter[str] = Counter()
    df: Counter[str] = Counter()
    for tokens in paper_tokens:
        in_paper: set[str] = set()
        for tok in tokens:
            if (
                MIN_ACRONYM_LENGTH <= len(tok) <= MAX_ACRONYM_LENGTH
                and tok.isupper()
                and tok.lower() not in _STOPWORDS
            ):
                tf[tok] += 1
                in_paper.add(tok)
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
    include_unigrams: int = 0,
    include_acronyms: int = 0,
    dedupe_substrings_flag: bool = False,
    anchor_phrase: str | None = None,
) -> DistillationResult:
    """Rank distinctive keywords from a corpus, optionally with MeSH."""
    paper_tokens: list[list[str]] = [_tokenize(p.combined) for p in papers]
    paper_stem_token_pairs: list[list[tuple[str, str]]] = [
        [(stem_key(t), t.lower()) for t in tokens] for tokens in paper_tokens
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
        tf, df, display = _foreground_stats_for(
            paper_stem_token_pairs, n, filter_content=True
        )
        bg_counts, total_bg = baseline_by_n[n - 1]
        rankings[n] = _rank_terms(
            RankingInputs(
                fg_counts=tf,
                fg_doc_freq=df,
                total_fg=sum(tf.values()),
                bg_counts=bg_counts,
                total_bg=total_bg,
            ),
            min_df=min_df,
            top_n=top_n,
            min_llr=min_llr,
            display=display,
        )

    # Acronyms — surface-form keys (no stemming).
    acro_tf, acro_df = _foreground_acronyms(paper_tokens)
    acronyms = _rank_terms(
        RankingInputs(
            fg_counts=acro_tf,
            fg_doc_freq=acro_df,
            total_fg=sum(acro_tf.values()),
            bg_counts=baseline.acronyms if baseline is not None else None,
            total_bg=baseline.total_acronyms if baseline is not None else None,
        ),
        min_df=min_df,
        top_n=top_n,
        min_llr=min_llr,
    )

    mesh_terms: list[KeywordScore] = []
    if mesh_descriptors:
        mesh_terms = aggregate_mesh(mesh_descriptors, top_n=mesh_top)

    return DistillationResult(
        papers=len(papers),
        unigrams=rankings[1],
        bigrams=rankings[2],
        trigrams=rankings[3],
        acronyms=acronyms,
        mesh_terms=mesh_terms,
        query_variants=build_query_variants(
            mesh_terms=mesh_terms,
            bigrams=rankings[2],
            trigrams=rankings[3],
            mesh_top=mesh_top,
            phrase_top=phrase_top,
            unigrams=rankings[1],
            acronyms=acronyms,
            include_unigrams=include_unigrams,
            include_acronyms=include_acronyms,
            dedupe_substrings_flag=dedupe_substrings_flag,
            anchor_phrase=anchor_phrase,
        ),
    )


# ---------------------------------------------------------------------------
# OUTPUT FORMATTING
# ---------------------------------------------------------------------------


def _merged_phrases(
    bigrams: list[KeywordScore],
    trigrams: list[KeywordScore],
    *,
    phrase_top: int | None = None,
    unigrams: list[KeywordScore] | None = None,
    acronyms: list[KeywordScore] | None = None,
    include_unigrams: int = 0,
    include_acronyms: int = 0,
    dedupe_substrings_flag: bool = False,
) -> list[KeywordScore]:
    """Build the keyword pool for the Title/Abstract clause.

    ``phrase_top`` caps the bigram+trigram contribution *before*
    ``include_unigrams`` / ``include_acronyms`` append extras, so the
    extras are additive rather than competing for the same slot count.
    """
    phrases = sorted(
        trigrams + bigrams,
        key=lambda k: (-k.llr, -k.document_frequency, k.term),
    )
    if phrase_top is not None and not dedupe_substrings_flag:
        phrases = phrases[: max(0, phrase_top)]

    extras: list[KeywordScore] = []
    if include_unigrams > 0 and unigrams:
        extras.extend(unigrams[:include_unigrams])
    if include_acronyms > 0 and acronyms:
        extras.extend(acronyms[:include_acronyms])

    pool = phrases + extras
    pool.sort(key=lambda k: (-k.llr, -k.document_frequency, k.term))

    if dedupe_substrings_flag and pool:
        from scripts._query_diagnose import dedupe_substrings

        # Dedupe over rendered PubMed text (not raw terms) so duplicate
        # clauses can't slip in under different surface strings.
        sanitized_terms = [_pubmed_phrase_text(k.term) for k in pool]
        kept_counts = Counter(dedupe_substrings(sanitized_terms))
        deduped_pool: list[KeywordScore] = []
        for score, sanitized in zip(pool, sanitized_terms, strict=True):
            if not sanitized or kept_counts[sanitized] <= 0:
                continue
            deduped_pool.append(score)
            kept_counts[sanitized] -= 1
        pool = deduped_pool
        if phrase_top is not None:
            phrase_limit = max(0, phrase_top)
            phrase_count = 0
            extra_ids = {id(score) for score in extras}
            capped_pool: list[KeywordScore] = []
            for score in pool:
                is_extra = id(score) in extra_ids
                if is_extra:
                    capped_pool.append(score)
                    continue
                if phrase_count >= phrase_limit:
                    continue
                capped_pool.append(score)
                phrase_count += 1
            pool = capped_pool

    return pool


def _pubmed_phrase_text(term: str) -> str:
    """Return the sanitized phrase text used inside PubMed quotes.

    PubMed phrase syntax is quote-delimited. Terms are normally generated by
    tokenizers or MeSH descriptors, but direct callers and future descriptors
    can still contain embedded quotes/newlines; sanitize those so one bad term
    cannot unbalance the whole Boolean query.
    """
    return _collapse_whitespace(term.replace('"', " "))


def _pubmed_clause(term: str, field: str) -> str:
    """Return one quoted PubMed clause, or "" when the term is empty."""
    cleaned = _pubmed_phrase_text(term)
    if not cleaned:
        return ""
    return f'"{cleaned}"[{field}]'


def _format_pubmed_query(
    scores: list[KeywordScore],
    *,
    top: int,
    field: str,
) -> str:
    if top <= 0:
        return ""
    clauses: list[str] = []
    seen: set[str] = set()
    for score in scores:
        clause = _pubmed_clause(score.term, field)
        dedupe_key = clause.casefold()
        if not clause or dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        clauses.append(clause)
        if len(clauses) >= top:
            break
    return " OR ".join(clauses)


def format_titleabstract_query(scores: list[KeywordScore], top: int) -> str:
    """OR-joined ``[Title/Abstract]`` Boolean fragment."""
    return _format_pubmed_query(scores, top=top, field="Title/Abstract")


def format_mesh_query(scores: list[KeywordScore], top: int) -> str:
    """OR-joined ``[MeSH Terms]`` Boolean fragment."""
    return _format_pubmed_query(scores, top=top, field="MeSH Terms")


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


def format_hybrid_query(
    anchor_phrase: str,
    phrase_scores: list[KeywordScore],
    *,
    phrase_top: int,
) -> str:
    """``"anchor"[T/A] AND ("phrase" OR ...)`` Boolean fragment.

    Mirrors the production ``SVD_QUERY`` shape so a paper must contain
    the cSVD-defining anchor *and* at least one corpus-distinctive
    topic term. The anchor doubles as a precision filter against
    overloaded acronyms (e.g. ``SVS`` = Society for Vascular Surgery)
    and unanchored phrases (e.g. ``"genome-wide association study"``)
    that catch off-topic GWAS papers across unrelated diseases.

    Pool terms that are case-insensitive substrings of the anchor are
    dropped before rendering. PubMed phrase search is positional within
    the T/A field, so a paper matching the anchor already matches any
    substring of it — keeping those in the OR-clause makes the pool
    tautological and the rendered query misleading.

    Returns the bare anchor clause when the pool is empty (or fully
    subsumed by the anchor), and an empty string when the anchor itself
    sanitises away (defensive — the CLI layer already validates
    non-empty input).
    """
    anchor_clause = _pubmed_clause(anchor_phrase, "Title/Abstract")
    if not anchor_clause:
        return ""
    anchor_lower = _pubmed_phrase_text(anchor_phrase).casefold()
    filtered = [
        s
        for s in phrase_scores
        if _pubmed_phrase_text(s.term).casefold() not in anchor_lower
    ]
    pool_clause = format_titleabstract_query(filtered, phrase_top)
    if not pool_clause:
        return anchor_clause
    return f"{anchor_clause} AND ({pool_clause})"


def build_query_variants(
    *,
    mesh_terms: list[KeywordScore],
    bigrams: list[KeywordScore],
    trigrams: list[KeywordScore],
    mesh_top: int,
    phrase_top: int,
    unigrams: list[KeywordScore] | None = None,
    acronyms: list[KeywordScore] | None = None,
    include_unigrams: int = 0,
    include_acronyms: int = 0,
    dedupe_substrings_flag: bool = False,
    anchor_phrase: str | None = None,
) -> dict[str, str]:
    pool = _merged_phrases(
        bigrams,
        trigrams,
        phrase_top=phrase_top,
        unigrams=unigrams,
        acronyms=acronyms,
        include_unigrams=include_unigrams,
        include_acronyms=include_acronyms,
        dedupe_substrings_flag=dedupe_substrings_flag,
    )
    # When extras are appended (unigrams/acronyms), the pool already
    # contains the desired entries; pass len(pool) so the caller's
    # ``top`` cap doesn't truncate the additions away.
    pool_cap = max(phrase_top, len(pool)) if pool else phrase_top
    hybrid = (
        format_hybrid_query(anchor_phrase, pool, phrase_top=pool_cap)
        if anchor_phrase
        else ""
    )
    return {
        _QUERY_FORMAT_STRUCTURED: format_structured_query(
            mesh_terms, pool, mesh_top=mesh_top, phrase_top=pool_cap
        ),
        _QUERY_FORMAT_MESH: format_mesh_query(mesh_terms, mesh_top),
        _QUERY_FORMAT_TITLEABSTRACT: format_titleabstract_query(pool, pool_cap),
        _QUERY_FORMAT_HYBRID: hybrid,
    }


def _query_formats_to_emit(result: DistillationResult, query_format: str) -> list[str]:
    if query_format != _QUERY_FORMAT_ALL:
        return [query_format]
    # Hybrid is only present when --anchor-phrase was passed; downstream
    # callers skip empty-string entries, so listing it here is safe even
    # if the anchor was omitted.
    if not result.mesh_terms:
        formats: list[str] = [_QUERY_FORMAT_TITLEABSTRACT]
    else:
        formats = [
            _QUERY_FORMAT_STRUCTURED,
            _QUERY_FORMAT_MESH,
            _QUERY_FORMAT_TITLEABSTRACT,
        ]
    if result.query_variants.get(_QUERY_FORMAT_HYBRID):
        formats.append(_QUERY_FORMAT_HYBRID)
    return formats


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
        _print_section("Top MeSH headings", result.mesh_terms, stream, show_llr=False)
    _print_section("Top distinctive phrases (bigrams)", result.bigrams, stream)
    _print_section("Top distinctive phrases (trigrams)", result.trigrams, stream)
    _print_section("Top distinctive unigrams", result.unigrams, stream)
    _print_section("Acronyms", result.acronyms, stream)

    variants = result.query_variants
    formats = _query_formats_to_emit(result, query_format)
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
# RICH RENDERING (TTY stdout path)
# ---------------------------------------------------------------------------


def _render_score_table(
    scores: list[KeywordScore],
    *,
    show_llr: bool = True,
) -> Table:
    """Build a rich Table for one keyword section.

    The section heading is rendered by the caller, not via ``Table.title``:
    auto-sized narrow tables (short terms) would otherwise force Rich to
    wrap the heading across two lines.
    """
    table = Table(
        box=box.HEAVY_HEAD,
        header_style="bold",
        show_edge=True,
        expand=False,
    )
    table.add_column("Term", style="white", overflow="fold")
    table.add_column("DF", justify="right", style="dim")
    table.add_column("TF", justify="right", style="dim")
    if show_llr:
        table.add_column("LLR", justify="right", style=f"bold {_ACCENT_COLOR}")

    for s in scores:
        row = [s.term, str(s.document_frequency), str(s.total_count)]
        if show_llr:
            row.append(f"{s.llr:.2f}")
        table.add_row(*row)
    return table


def _render_query(query: str) -> Text:
    """Style a PubMed Boolean query string for terminal display."""
    text = Text()
    last = 0
    for m in _QUERY_TOKEN_RE.finditer(query):
        if m.start() > last:
            text.append(query[last : m.start()])
        group = m.lastgroup or "bare"
        text.append(m.group(), style=_QUERY_TOKEN_STYLES.get(group, ""))
        last = m.end()
    if last < len(query):
        text.append(query[last:])
    return text


def _render_config_panel(args: argparse.Namespace, *, phrase_top: int) -> Panel:
    """Compact key-value panel summarising the resolved CLI args."""
    grid = Table.grid(padding=(0, 2))
    grid.add_column(justify="right", style="dim")
    grid.add_column(style="bold")

    grid.add_row("xml-dir", str(args.xml_dir))
    grid.add_row(
        "top-n / min-df / min-llr",
        f"{args.top_n}  /  {args.min_df}  /  {args.min_llr:.2f}",
    )
    grid.add_row(
        "mesh-top / phrase-top",
        f"{args.mesh_top}  /  {phrase_top}",
    )
    grid.add_row("mesh", "on" if not args.no_mesh else "off")
    grid.add_row("baseline-cache", str(args.baseline_cache))
    grid.add_row("query-format", args.query_format)
    if args.anchor_phrase:
        grid.add_row("anchor-phrase", args.anchor_phrase)

    return Panel(
        grid,
        title=f"[bold {_PRIMARY_COLOR}]distill_pubmed_keywords[/] — run config",
        border_style=_PRIMARY_COLOR,
        expand=False,
    )


def _render_rich_report(
    result: DistillationResult,
    *,
    query_format: str,
    console: Console,
) -> None:
    """Render the full keyword report to ``console`` with rich styling."""
    console.print()
    console.print(f"Distilled keywords from [bold]{result.papers}[/] paper(s).")

    sections: list[tuple[str, list[KeywordScore], bool]] = []
    if result.mesh_terms:
        sections.append(("Top MeSH headings", result.mesh_terms, False))
    sections.extend(
        [
            ("Top distinctive phrases (bigrams)", result.bigrams, True),
            ("Top distinctive phrases (trigrams)", result.trigrams, True),
            ("Top distinctive unigrams", result.unigrams, True),
            ("Acronyms", result.acronyms, True),
        ]
    )
    for title, scores, show_llr in sections:
        console.print()
        if not scores:
            console.print(f"[bold {_PRIMARY_COLOR}]{title}[/] — [dim](none)[/]")
            continue
        console.print(f"[bold {_PRIMARY_COLOR}]{title}[/] — showing {len(scores)}")
        console.print(_render_score_table(scores, show_llr=show_llr))

    variants = result.query_variants
    formats = _query_formats_to_emit(result, query_format)
    console.print()
    console.print(f"[bold {_PRIMARY_COLOR}]Suggested PubMed query[/]")
    for fmt in formats:
        q = variants.get(fmt, "")
        if not q:
            continue
        console.print(
            Panel(
                _render_query(q),
                title=f"format: [bold]{fmt}[/]",
                border_style=_ACCENT_COLOR,
                expand=False,
            )
        )

    # Plain-text rendering of the structured query at the very end so it
    # can be selected and pasted straight into PubMed. The panel above is
    # easier to read but its borders and ANSI styling can leak into the
    # clipboard; `markup=False` also stops Rich from interpreting the
    # bracketed field tags (e.g. `[Title/Abstract]`) as markup, and
    # `soft_wrap=True` keeps the whole query on a single logical line.
    structured = variants.get(_QUERY_FORMAT_STRUCTURED, "")
    if structured and _QUERY_FORMAT_STRUCTURED in formats:
        console.print()
        console.print(f"[bold {_PRIMARY_COLOR}]format: structured — copy-paste[/]")
        console.print(structured, markup=False, soft_wrap=True)


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
        raise argparse.ArgumentTypeError(f"value must be non-negative, got {parsed}")
    return parsed


def _non_negative_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"invalid float: {value!r}") from e
    if not math.isfinite(parsed):
        raise argparse.ArgumentTypeError(f"value must be finite, got {value!r}")
    if parsed < 0:
        raise argparse.ArgumentTypeError(f"value must be non-negative, got {parsed}")
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
            f"Minimum document frequency to keep a keyword (default: {DEFAULT_MIN_DF})"
        ),
    )
    parser.add_argument(
        "--min-llr",
        type=_non_negative_float,
        default=DEFAULT_MIN_LLR,
        help=(
            "Minimum log-likelihood ratio for inclusion "
            f"(default: {DEFAULT_MIN_LLR:.2f}, heuristic threshold)"
        ),
    )
    parser.add_argument(
        "--phrase-top",
        type=_non_negative_int,
        default=DEFAULT_PHRASE_TOP,
        help=(
            f"Phrases included in the suggested query (default: {DEFAULT_PHRASE_TOP})"
        ),
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
        default=_QUERY_FORMAT_ALL,
        help="Which query variant(s) to emit (default: all)",
    )
    parser.add_argument(
        "--anchor-phrase",
        type=str,
        default=None,
        metavar="PHRASE",
        help=(
            "Anchor phrase for the 'hybrid' query variant — rendered as "
            '"<PHRASE>"[Title/Abstract] AND ("topic" OR ...). Supports '
            "4-grams the n-gram extractor can't surface (e.g. "
            "'cerebral small vessel disease'). Required when "
            "--query-format=hybrid; ignored otherwise."
        ),
    )
    parser.add_argument(
        "--no-mesh",
        action="store_true",
        help="Skip MeSH harvest; emit only the Title/Abstract query variant.",
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
    parser.add_argument(
        "--include-unigrams",
        type=_non_negative_int,
        default=0,
        metavar="N",
        help=(
            "Append top-N high-LLR unigrams to the Title/Abstract clause "
            "(default: 0, omitted)."
        ),
    )
    parser.add_argument(
        "--include-acronyms",
        type=_non_negative_int,
        default=0,
        metavar="M",
        help=(
            "Append top-M high-LLR acronyms (e.g. CADASIL, NOTCH3, GWAS) "
            "to the Title/Abstract clause (default: 0, omitted)."
        ),
    )
    parser.add_argument(
        "--dedupe-substrings",
        action="store_true",
        help=(
            "Drop Title/Abstract phrases that are case-insensitive "
            "substrings of longer kept phrases (e.g. drop 'small vessel' "
            "when 'small vessel disease' is already in the clause)."
        ),
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help=(
            "Compare the distilled query against pipeline.pubmed_search."
            "SVD_QUERY by running both through NCBI esearch. Emits a "
            "Markdown report instead of the standard keyword tables."
        ),
    )
    parser.add_argument(
        "--diagnose-since",
        type=str,
        default=None,
        metavar="YYYY/MM/DD",
        help=(
            "Earliest publication date for --diagnose comparison "
            "(default: five years before today)."
        ),
    )
    parser.add_argument(
        "--diagnose-until",
        type=str,
        default=None,
        metavar="YYYY/MM/DD",
        help=(
            "Latest publication date for --diagnose comparison (default: "
            "no upper bound). Combine with --diagnose-since to slice a "
            "narrow window when broad queries hit PubMed's 9,999-record "
            "cap."
        ),
    )
    parser.add_argument(
        "--diagnose-top-k",
        type=_non_negative_int,
        default=15,
        metavar="K",
        help=(
            "Number of papers from each side of the overlap to show in "
            "the --diagnose report (default: 15)."
        ),
    )
    parser.add_argument(
        "--diagnose-retmax",
        type=_non_negative_int,
        default=None,
        metavar="N",
        help=(
            "Maximum PMIDs to fetch per --diagnose esearch call (default "
            "and max: 9999 — PubMed's hard cap, larger values are clamped). "
            "Lower for fast sanity checks; to get past the cap on broad "
            "queries, narrow --diagnose-since instead."
        ),
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help=(
            "Score the distilled query for cSVD-relevance (empirical MeSH "
            "match + Claude LLM fallback) and emit a Markdown report plus "
            "a JSON sidecar."
        ),
    )
    parser.add_argument(
        "--validate-sample",
        type=_non_negative_int,
        default=None,
        metavar="N",
        help=("Random sample size per query for --validate scoring (default: 200)."),
    )
    parser.add_argument(
        "--validate-since",
        type=str,
        default=None,
        metavar="YYYY/MM/DD",
        help=(
            "Earliest publication date for --validate sampling "
            "(default: two years before today, so most papers have "
            "NLM MeSH assigned)."
        ),
    )
    parser.add_argument(
        "--validate-until",
        type=str,
        default=None,
        metavar="YYYY/MM/DD",
        help="Latest publication date for --validate sampling (default: today).",
    )
    parser.add_argument(
        "--validate-llm-model",
        type=str,
        default=None,
        metavar="MODEL",
        help=(
            "Anthropic model for the --validate LLM fallback (default: "
            "the module's DEFAULT_LLM_MODEL — Claude Haiku 4.5)."
        ),
    )
    parser.add_argument(
        "--validate-mesh-threshold",
        type=_non_negative_int,
        default=None,
        metavar="N",
        help=(
            "Bibliography-paper count for a MeSH term to count as "
            "cSVD-relevant during --validate (default: 3)."
        ),
    )
    parser.add_argument(
        "--validate-no-llm-fallback",
        action="store_true",
        help=(
            "Skip the LLM fallback for --validate; unindexed (no-MeSH) "
            "papers are reported as 'unscoreable' instead."
        ),
    )
    parser.add_argument(
        "--validate-seed",
        type=_non_negative_int,
        default=None,
        metavar="N",
        help=(
            "Random seed for --validate sample selection (default: 0). "
            "Pin different values to compare multiple stratifications."
        ),
    )
    parser.add_argument(
        "--validate-output",
        type=Path,
        default=None,
        metavar="DIR_OR_PATH",
        help=(
            "Where to write the --validate Markdown report. If a "
            "directory (default: logs/query_validate/), a timestamped "
            "filename is generated and a sibling .json sidecar is "
            "written. If a .md file path, the JSON sidecar is named "
            "alongside it."
        ),
    )
    args = parser.parse_args(argv)
    if args.build_baseline:
        return args
    if args.anchor_phrase is not None:
        args.anchor_phrase = args.anchor_phrase.strip()
        if not args.anchor_phrase:
            parser.error("--anchor-phrase must not be empty or whitespace-only")
    if args.query_format == _QUERY_FORMAT_HYBRID and not args.anchor_phrase:
        parser.error("--query-format=hybrid requires --anchor-phrase")
    if args.diagnose and args.validate:
        parser.error("--diagnose and --validate cannot be used together")
    return args


def _structural_issues_for_diagnose(args: argparse.Namespace) -> list[str]:
    """Bullets describing remaining structural weaknesses given the run config.

    Each item names the flag that fixes it so the diagnose report points
    the reader at concrete next steps. Items already mitigated by the
    current flag set are omitted.
    """
    issues: list[str] = []
    if args.include_unigrams == 0:
        issues.append(
            "Phrase-only Title/Abstract clause excludes high-LLR unigrams "
            "(e.g. `notch3`, `leukoencephalopathy`). "
            "Fix: `--include-unigrams N`."
        )
    if args.include_acronyms == 0:
        issues.append(
            "ALL-CAPS acronyms (CADASIL, NOTCH3, GWAS, WMH) are ranked "
            "but never appear in the query. "
            "Fix: `--include-acronyms M`."
        )
    if not args.dedupe_substrings:
        issues.append(
            'Substring-redundant phrases (e.g. `"small vessel"` + '
            '`"small vessel disease"` + `"vessel disease"`) inflate '
            "the T/A clause. Fix: `--dedupe-substrings`."
        )
    if not issues:
        issues.append("All three current structural fixes are enabled in this run.")
    return issues


def _run_diagnose(args: argparse.Namespace, result: DistillationResult) -> int:
    """Run the PubMed comparison diagnostic and emit a Markdown report."""
    try:
        from pipeline.pubmed_search import SVD_QUERY
    except ImportError as exc:
        logger.error(f"Could not import SVD_QUERY for diagnostic comparison: {exc}")
        return 1
    from scripts._query_diagnose import run_diagnose

    variant = _resolve_query_variant(args, result)
    distilled_query = result.query_variants.get(variant, "")
    if not distilled_query:
        logger.error(
            f"No '{variant}' query variant available for diagnosis. Run "
            "without --diagnose to inspect keyword tables, or set "
            "--query-format explicitly."
        )
        return 1

    diagnose_since = args.diagnose_since
    if diagnose_since is None:
        today = _dt.date.today()
        diagnose_since = f"{today.year - 5}/01/01"

    structural_issues = _structural_issues_for_diagnose(args)

    try:
        markdown = run_diagnose(
            distilled_query=distilled_query,
            production_query=SVD_QUERY,
            distilled_label=f"distilled ({variant})",
            production_label="SVD_QUERY",
            diagnose_since=diagnose_since,
            diagnose_until=args.diagnose_until,
            retmax=args.diagnose_retmax,
            top_k=args.diagnose_top_k,
            structural_issues=structural_issues,
        )
    except (OSError, RuntimeError) as exc:
        logger.error(f"Diagnostic failed: {exc}")
        return 1

    if args.output:
        try:
            args.output.write_text(markdown, encoding="utf-8")
        except OSError as exc:
            logger.error(f"Could not write diagnostic to {args.output}: {exc}")
            return 1
        logger.info(f"Wrote diagnostic report to {args.output}")
    else:
        print(markdown)
    return 0


def _resolve_validate_output_paths(
    output_arg: Path | None,
) -> tuple[Path, Path]:
    """Resolve --validate-output into (markdown_path, json_path).

    Both paths share the same stem so the JSON sidecar lives next to
    its Markdown report. A bare directory (or the default) gets a
    timestamped filename; an explicit ``.md`` path is used verbatim.
    """
    from scripts._query_validate import DEFAULT_VALIDATE_OUTPUT_DIR

    timestamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    default_name = f"query_validate_{timestamp}"

    if output_arg is None:
        output_arg = DEFAULT_VALIDATE_OUTPUT_DIR
    if output_arg.is_dir() or output_arg.suffix == "":
        base = output_arg / default_name
        return base.with_suffix(".md"), base.with_suffix(".json")
    if output_arg.suffix.casefold() == ".json":
        return output_arg.with_suffix(".md"), output_arg
    return output_arg, output_arg.with_suffix(".json")


def _resolve_query_variant(args: argparse.Namespace, result: DistillationResult) -> str:
    """Variant name to use when ``--query-format=all`` is in effect.

    Prefers hybrid (closest structurally to ``SVD_QUERY``) over structured
    over plain titleabstract; an explicit ``--query-format`` is honored.
    Shared by ``_run_diagnose`` and ``_run_validate``.
    """
    if args.query_format != _QUERY_FORMAT_ALL:
        return args.query_format
    if result.query_variants.get(_QUERY_FORMAT_HYBRID):
        return _QUERY_FORMAT_HYBRID
    if result.mesh_terms:
        return _QUERY_FORMAT_STRUCTURED
    return _QUERY_FORMAT_TITLEABSTRACT


def _run_validate(args: argparse.Namespace, result: DistillationResult) -> int:
    """Run the relevance validation and emit Markdown + JSON sidecar."""
    from scripts._query_validate import (
        DEFAULT_LLM_MODEL,
        DEFAULT_VALIDATE_MESH_THRESHOLD,
        DEFAULT_VALIDATE_SAMPLE,
        DEFAULT_VALIDATE_SEED,
        emit_validate_json,
        render_validate_report,
        run_validate,
    )

    variant_used = _resolve_query_variant(args, result)
    distilled_query = result.query_variants.get(variant_used, "")
    if not distilled_query:
        logger.error(
            "No distilled query variant available for validation. "
            "Run without --validate to inspect keyword tables, or set "
            "--query-format explicitly."
        )
        return 1

    if args.validate_since is None:
        today = _dt.date.today()
        # Two years gives most papers time to be MeSH-indexed; recent
        # papers (last few months) tend not to be.
        validate_since = f"{today.year - 2:04d}/{today.month:02d}/{today.day:02d}"
    else:
        validate_since = args.validate_since

    sample_size = (
        args.validate_sample
        if args.validate_sample is not None
        else DEFAULT_VALIDATE_SAMPLE
    )
    mesh_threshold = (
        args.validate_mesh_threshold
        if args.validate_mesh_threshold is not None
        else DEFAULT_VALIDATE_MESH_THRESHOLD
    )
    seed = (
        args.validate_seed if args.validate_seed is not None else DEFAULT_VALIDATE_SEED
    )
    llm_model = args.validate_llm_model or DEFAULT_LLM_MODEL

    try:
        report = run_validate(
            query=distilled_query,
            label=f"distilled ({variant_used})",
            sample_size=sample_size,
            seed=seed,
            validate_since=validate_since,
            validate_until=args.validate_until,
            mesh_threshold=mesh_threshold,
            llm_model=llm_model,
            use_llm_fallback=not args.validate_no_llm_fallback,
            mesh_dir=args.mesh_cache,
        )
    except (OSError, RuntimeError, FileNotFoundError) as exc:
        logger.error(f"Validation failed: {exc}")
        return 1

    try:
        md_path, json_path = _resolve_validate_output_paths(args.validate_output)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        markdown = render_validate_report(report)
        md_path.write_text(markdown, encoding="utf-8")
        emit_validate_json(report, json_path)
    except OSError as exc:
        logger.error(f"Could not write validation output: {exc}")
        return 1
    logger.info(f"Wrote validation report to {md_path}")
    logger.info(f"Wrote validation JSON sidecar to {json_path}")
    return 0


def _build_progress() -> Progress:
    """Construct the rich.Progress used for slow network/IO stages.

    Shares ``_stderr_console`` with RichHandler so log lines emitted
    inside the progress context (warnings from fetch loops, etc.) are
    coordinated with the Live renderer — without this, warnings would
    paint raw bytes through the active bar.
    """
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=_stderr_console,
        transient=False,
    )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[
            RichHandler(
                console=_stderr_console,
                rich_tracebacks=True,
                # Off because our log payloads interpolate user-controlled
                # values (file names, PMIDs, error messages from Entrez).
                markup=False,
                show_path=False,
                omit_repeated_times=False,
            )
        ],
    )
    args = _parse_args(argv)
    from dotenv import load_dotenv  # python-dotenv pinned in pyproject.toml

    load_dotenv()

    phrase_top = int(args.phrase_top)

    # The config panel is only useful for interactive runs. Suppress it
    # for --build-baseline, file output, and any non-TTY stdout (pipes,
    # redirects) so machine consumers see clean output.
    show_panel = (
        _console.is_terminal
        and not args.build_baseline
        and args.output is None
        and not args.json
    )
    if show_panel:
        _console.print(_render_config_panel(args, phrase_top=phrase_top))

    if args.build_baseline:
        pdat_range = args.baseline_pdat_range or f"2020:{_dt.date.today().year}"
        try:
            with _build_progress() as progress:
                task = progress.add_task(
                    "Building PubMed baseline", total=args.baseline_size
                )
                build_baseline_cache(
                    size=args.baseline_size,
                    output_path=args.baseline_cache,
                    pdat_range=pdat_range,
                    progress_callback=lambda c, t: progress.update(
                        task, completed=c, total=t
                    ),
                )
        except (OSError, RuntimeError, ValueError) as e:
            logger.error(str(e))
            return 1
        return 0

    with _build_progress() as progress:
        load_task = progress.add_task("Loading corpus", total=None)
        try:
            papers = load_corpus(
                args.xml_dir,
                progress_callback=lambda c, t: progress.update(
                    load_task, completed=c, total=t
                ),
            )
        except FileNotFoundError as e:
            logger.error(str(e))
            return 1

        if not papers:
            logger.error("No parseable papers found.")
            return 1

        try:
            baseline = load_baseline_cache(args.baseline_cache)
        except FileNotFoundError as e:
            logger.error(str(e))
            return 1
        except RuntimeError as e:
            logger.error(str(e))
            return 1

        valid_pmids = _unique_valid_pmids(p.pmid for p in papers if p.pmid)

        mesh_map: dict[str, list[MeshDescriptor]] | None = None
        if not args.no_mesh and valid_pmids:
            mesh_task = progress.add_task("Fetching MeSH terms", total=len(valid_pmids))
            try:
                mesh_map = fetch_mesh_terms(
                    valid_pmids,
                    args.mesh_cache,
                    progress_callback=lambda c, t: progress.update(
                        mesh_task, completed=c, total=t
                    ),
                )
            except (OSError, RuntimeError) as e:
                logger.warning(
                    f"MeSH harvest disabled — {e}. Run with --no-mesh to silence."
                )

    result = distill_keywords(
        papers,
        baseline=baseline,
        top_n=args.top_n,
        min_df=args.min_df,
        min_llr=args.min_llr,
        mesh_descriptors=mesh_map,
        mesh_top=args.mesh_top,
        phrase_top=phrase_top,
        include_unigrams=args.include_unigrams,
        include_acronyms=args.include_acronyms,
        dedupe_substrings_flag=args.dedupe_substrings,
        anchor_phrase=args.anchor_phrase,
    )

    if args.diagnose:
        return _run_diagnose(args, result)

    if args.validate:
        return _run_validate(args, result)

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
    elif _console.is_terminal:
        _render_rich_report(
            result,
            query_format=args.query_format,
            console=_console,
        )
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
    # When invoked as `python scripts/distill_pubmed_keywords.py`, Python
    # sets sys.path[0] to the script's directory, which prevents the
    # cross-module imports below (`scripts._query_diagnose`,
    # `pipeline.pubmed_search`) from resolving. Re-add the project root so
    # both packages are importable, then run.
    _project_root = str(Path(__file__).resolve().parent.parent)
    if _project_root not in sys.path:
        sys.path.insert(0, _project_root)
    sys.exit(main())
