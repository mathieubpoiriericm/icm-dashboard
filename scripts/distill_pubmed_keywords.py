"""Distill candidate PubMed search keywords from MODS bibliography XML files.

Walks every *.xml file in the input directory (default: data/bibentry/xml/),
parses titles and abstracts from the MODS schema, then ranks unigrams,
bigrams, and trigrams by document frequency (number of distinct papers
containing the term). English stopwords, scientific filler, and structured
abstract section labels are filtered out. Detected ALL-CAPS acronyms are
surfaced separately. A ready-to-paste PubMed [Title/Abstract] query fragment
is generated from the top phrases.

Usage:
    python scripts/distill_pubmed_keywords.py
    python scripts/distill_pubmed_keywords.py --xml-dir data/bibentry/xml
    python scripts/distill_pubmed_keywords.py --top-n 50 --min-df 3
    python scripts/distill_pubmed_keywords.py --query-top 20 --json
    python scripts/distill_pubmed_keywords.py --output keywords.txt
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TextIO

from lxml import etree  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CONSTANTS
# ---------------------------------------------------------------------------

# Resolve the default corpus path relative to the script so the CLI works
# regardless of the caller's working directory.
_PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
DEFAULT_XML_DIR: Final[Path] = _PROJECT_ROOT / "data" / "bibentry" / "xml"
DEFAULT_TOP_N: Final[int] = 30
DEFAULT_MIN_DF: Final[int] = 2
DEFAULT_QUERY_TOP: Final[int] = 15

MIN_TOKEN_LENGTH: Final[int] = 3
MIN_ACRONYM_LENGTH: Final[int] = 2
MAX_ACRONYM_LENGTH: Final[int] = 6

# Letter-led tokens that may carry intra-word hyphens. Hyphens must be
# followed by alphanumerics, which keeps "follow-up" intact but stops
# "TGF-" / "age-" / "end-" from leaking through as content tokens when the
# source text has a stray trailing hyphen before punctuation.
_TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*"
)
_NUMERIC_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[\d\-]+$")

# Namespace wildcard — MODS files declare xmlns="http://www.loc.gov/mods/v3"
# but using {*} keeps the queries robust if a record is namespace-stripped.
_NS: Final[str] = "{*}"

# Same hardening as pipeline.config.SAFE_XML_PARSER; inlined here so the
# script stays runnable without putting the pipeline package on sys.path.
_SAFE_PARSER: Final[etree.XMLParser] = etree.XMLParser(
    resolve_entities=False, no_network=True
)

# English stopwords + scientific filler + structured-abstract section labels.
# Lowercased; matched after lowercasing each token.
_STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        # Articles, prepositions, conjunctions
        "a", "an", "the", "and", "or", "but", "if", "of", "at", "by", "for",
        "with", "about", "against", "between", "into", "through", "during",
        "before", "after", "above", "below", "to", "from", "up", "down",
        "in", "out", "on", "off", "over", "under", "than", "then", "once",
        "here", "there", "when", "where", "why", "how", "as", "because",
        "while", "although", "though", "since", "unless", "until", "whether",
        "via", "across", "within", "without", "among", "per", "upon",
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
        # Adverbs / quantifiers / very-common modifiers
        "all", "any", "both", "each", "few", "more", "most", "other", "some",
        "such", "nor", "not", "only", "own", "same", "too", "very", "just",
        "also", "well", "however", "moreover", "thus", "hence", "rather",
        "much", "many", "less", "least", "either", "neither", "yet", "still",
        "respectively", "previously", "currently", "approximately", "non",
        "due", "given", "non-", "vs",
        # Generic scientific filler — words that say nothing about subject matter
        "study", "studies", "paper", "papers", "research", "result", "results",
        "method", "methods", "methodology", "conclusion", "conclusions",
        "finding", "findings", "analysis", "analyses", "evaluate", "evaluated",
        "evaluation", "observe", "observed", "observation", "observations",
        "demonstrate", "demonstrated", "show", "shows", "shown", "showed",
        "find", "found", "suggest", "suggests", "suggested", "indicate",
        "indicates", "indicated", "perform", "performed", "performs",
        "include", "included", "includes", "including", "present", "presents",
        "presented", "presenting", "use", "used", "uses", "using", "based",
        "compare", "compared", "comparison", "comparing", "increase",
        "increased", "increases", "decrease", "decreased", "decreases",
        "higher", "lower", "high", "low", "significant", "significantly",
        "association", "associated", "associations", "correlate",
        "correlated", "correlation", "correlations", "group", "groups",
        "patient", "patients", "subject", "subjects", "participant",
        "participants", "control", "controls", "case", "cases", "baseline",
        "follow", "year", "years", "age", "ages", "aged", "level", "levels",
        "mean", "median", "range", "ratio", "value", "values", "data",
        "total", "large", "small", "new", "novel", "report", "reports",
        "reported", "review", "reviews", "reviewed", "type", "types",
        "form", "forms", "role", "roles", "term", "terms", "number",
        "numbers", "rate", "rates", "effect", "effects", "factor", "factors",
        "test", "tests", "tested", "testing",
        "examined", "assess", "assessed", "assessment",
        "investigation", "investigated", "investigate", "important",
        "potential", "possible", "various", "several", "different",
        "common", "general", "specific", "overall", "current", "recent",
        "able", "likely", "unlikely", "appear", "appeared",
        "appears", "remained", "remain", "remains",
        # Structured abstract section labels (often embedded mid-text)
        "background", "purpose", "objective", "objectives", "aim", "aims",
        "introduction", "discussion", "interpretation", "design", "setting",
        "interventions", "measurements", "outcomes", "outcome", "context",
        # Statistical filler
        "confidence", "interval", "intervals", "odds", "risk", "hazard",
        "regression", "model", "models", "modeled", "modeling", "estimated",
        "estimate", "estimates", "calculated", "calculate", "calculation",
        "calculations", "deviation", "standard", "variance",
        "probability", "probabilities",
        # Number words
        "one", "two", "three", "four", "five", "six", "seven", "eight",
        "nine", "ten", "first", "second", "third", "fourth", "fifth",
    }
)


# ---------------------------------------------------------------------------
# DATACLASSES
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PaperText:
    """Title + abstract for one paper."""

    pmid: str | None
    title: str
    abstract: str

    @property
    def combined(self) -> str:
        return f"{self.title} {self.abstract}".strip()


@dataclass(slots=True)
class KeywordScore:
    """A candidate keyword with its document and total frequency."""

    term: str
    document_frequency: int
    total_count: int


@dataclass(slots=True)
class DistillationResult:
    """The full set of ranked candidate keywords from a corpus."""

    papers: int
    unigrams: list[KeywordScore]
    bigrams: list[KeywordScore]
    trigrams: list[KeywordScore]
    acronyms: list[KeywordScore]


# ---------------------------------------------------------------------------
# XML PARSING
# ---------------------------------------------------------------------------


def _element_text(elem: etree._Element | None) -> str:
    """Concatenate all text within an element (handles mixed content)."""
    if elem is None:
        return ""
    return "".join(t for t in elem.itertext() if isinstance(t, str)).strip()


def parse_mods_file(path: Path) -> PaperText | None:
    """Parse one MODS XML file into a PaperText.

    Anchors at the inner <mods> element so titles inside <relatedItem>
    (the journal) don't leak into the article title.
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
    title_el = (
        title_info.find(f"./{_NS}title") if title_info is not None else None
    )
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
    """Parse every *.xml file in the directory into PaperText records."""
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


def _rank_ngrams(
    paper_tokens: list[list[str]],
    n: int,
    *,
    min_df: int,
    top_n: int,
) -> list[KeywordScore]:
    """Rank n-grams primarily by document frequency, breaking ties by total count.

    An n-gram is dropped if any of its tokens fails the content filter — this
    keeps stopwords out of multiword phrases too, so e.g. "white matter the"
    can't slip in while "white matter hyperintensities" can.
    """
    df_counter: Counter[tuple[str, ...]] = Counter()
    tf_counter: Counter[tuple[str, ...]] = Counter()

    for tokens in paper_tokens:
        clean = [
            gram
            for gram in _ngrams(tokens, n)
            if all(_is_content_token(t) for t in gram)
        ]
        tf_counter.update(clean)
        df_counter.update(set(clean))

    candidates = (
        KeywordScore(
            term=" ".join(gram),
            document_frequency=df_counter[gram],
            total_count=tf_counter[gram],
        )
        for gram in df_counter
        if df_counter[gram] >= min_df
    )
    return sorted(
        candidates,
        key=lambda k: (-k.document_frequency, -k.total_count, k.term),
    )[:top_n]


def _detect_acronyms(
    papers: list[PaperText],
    *,
    min_df: int,
    top_n: int,
) -> list[KeywordScore]:
    """Find ALL-CAPS short tokens — likely acronyms (GWAS, MRI, ICAM, SVD, ...)."""
    df_counter: Counter[str] = Counter()
    tf_counter: Counter[str] = Counter()

    for paper in papers:
        tokens = _tokenize(paper.combined)
        in_paper = {
            tok
            for tok in tokens
            if MIN_ACRONYM_LENGTH <= len(tok) <= MAX_ACRONYM_LENGTH
            and tok.isupper()
            and tok.lower() not in _STOPWORDS
        }
        tf_counter.update(tok for tok in tokens if tok in in_paper)
        df_counter.update(in_paper)

    candidates = (
        KeywordScore(
            term=tok,
            document_frequency=df_counter[tok],
            total_count=tf_counter[tok],
        )
        for tok in df_counter
        if df_counter[tok] >= min_df
    )
    return sorted(
        candidates,
        key=lambda k: (-k.document_frequency, -k.total_count, k.term),
    )[:top_n]


# ---------------------------------------------------------------------------
# DISTILLATION
# ---------------------------------------------------------------------------


def distill_keywords(
    papers: list[PaperText],
    *,
    top_n: int = DEFAULT_TOP_N,
    min_df: int = DEFAULT_MIN_DF,
) -> DistillationResult:
    """Rank unigrams, bigrams, trigrams, and acronyms from a paper corpus."""
    lower_tokens: list[list[str]] = [
        [t.lower() for t in _tokenize(p.combined)] for p in papers
    ]
    return DistillationResult(
        papers=len(papers),
        unigrams=_rank_ngrams(lower_tokens, 1, min_df=min_df, top_n=top_n),
        bigrams=_rank_ngrams(lower_tokens, 2, min_df=min_df, top_n=top_n),
        trigrams=_rank_ngrams(lower_tokens, 3, min_df=min_df, top_n=top_n),
        acronyms=_detect_acronyms(papers, min_df=min_df, top_n=top_n),
    )


# ---------------------------------------------------------------------------
# OUTPUT FORMATTING
# ---------------------------------------------------------------------------


def _merged_phrases(result: DistillationResult) -> list[KeywordScore]:
    """Bigrams + trigrams ranked together — used for the suggested query."""
    return sorted(
        result.trigrams + result.bigrams,
        key=lambda k: (-k.document_frequency, -k.total_count, k.term),
    )


def format_pubmed_query(scores: list[KeywordScore], top: int) -> str:
    """OR-joined PubMed [Title/Abstract] query fragment from the top phrases."""
    if not scores or top <= 0:
        return ""
    return " OR ".join(f'"{s.term}"[Title/Abstract]' for s in scores[:top])


def _print_section(
    title: str, scores: list[KeywordScore], stream: TextIO
) -> None:
    print(f"\n--- {title} (showing {len(scores)}) ---", file=stream)
    if not scores:
        print("(none)", file=stream)
        return
    width = max(len(s.term) for s in scores)
    print(f"{'TERM'.ljust(width)}  DF  TF", file=stream)
    for s in scores:
        print(
            f"{s.term.ljust(width)}  {s.document_frequency:>2}  "
            f"{s.total_count:>3}",
            file=stream,
        )


def write_text_report(
    result: DistillationResult,
    query_top: int,
    stream: TextIO,
) -> None:
    print(f"\nDistilled keywords from {result.papers} paper(s).", file=stream)
    _print_section("Unigrams", result.unigrams, stream)
    _print_section("Bigrams", result.bigrams, stream)
    _print_section("Trigrams", result.trigrams, stream)
    _print_section("Acronyms (ALL CAPS in source)", result.acronyms, stream)
    print(
        f"\n--- Suggested PubMed query fragment (top {query_top} phrases) ---",
        file=stream,
    )
    print(format_pubmed_query(_merged_phrases(result), query_top), file=stream)


def to_json(result: DistillationResult, query_top: int) -> str:
    def _scores(xs: list[KeywordScore]) -> list[dict[str, str | int]]:
        return [
            {
                "term": s.term,
                "document_frequency": s.document_frequency,
                "total_count": s.total_count,
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
            "pubmed_query": format_pubmed_query(_merged_phrases(result), query_top),
        },
        indent=2,
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _non_negative_int(value: str) -> int:
    """argparse type: parse an int and reject negatives.

    Without this, ``--top-n -5`` silently slips through Python's slice
    semantics (`scores[:-5]`) and returns "all but the last 5" instead of
    erroring — a confusing failure mode.
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


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Distill PubMed-API-ready keywords from MODS bibliography XML."
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
        "--query-top",
        type=_non_negative_int,
        default=DEFAULT_QUERY_TOP,
        help=(
            "Phrases included in the suggested PubMed query fragment "
            f"(default: {DEFAULT_QUERY_TOP})"
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


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    args = _parse_args(argv)

    try:
        papers = load_corpus(args.xml_dir)
    except FileNotFoundError as e:
        logger.error(str(e))
        return 1

    if not papers:
        logger.error("No parseable papers found.")
        return 1

    result = distill_keywords(papers, top_n=args.top_n, min_df=args.min_df)

    if args.json:
        payload = to_json(result, args.query_top)
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
                write_text_report(result, args.query_top, f)
        except OSError as e:
            logger.error(f"Could not write to {args.output}: {e}")
            return 1
        logger.info(f"Wrote text report to {args.output}")
    else:
        write_text_report(result, args.query_top, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
