"""PubMed query diagnostic — comparison harness for distilled vs curated queries.

Sister module to ``scripts/distill_pubmed_keywords.py``. Compares a
generated PubMed query against the curated ``SVD_QUERY`` in
``pipeline/pubmed_search.py`` by:

- Submitting each through NCBI ``esearch`` and collecting PMIDs
- Computing overlap of the two PMID sets
- Fetching title/journal/year/MeSH for sampled differing PMIDs via
  ``efetch``
- Rendering a Markdown report with overlap counts, sample tables, and
  an interpretation guide

Network calls reuse ``_configure_entrez`` / ``_ncbi_retry`` /
``_ncbi_sleep`` from the parent distill script (imported lazily inside
the fetcher functions so unit tests that inject a ``fetcher`` callable
never trigger an Entrez import).

The module also hosts ``dedupe_substrings``, a pure helper used by the
distill script's ``--dedupe-substrings`` fix.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from lxml import etree  # type: ignore[import-untyped]

# Helpers reused from the sister distill script. These are pure
# constants/functions (no Bio.Entrez side effects), so a top-level
# import is safe; the Entrez-touching helpers are still imported lazily
# inside the default fetchers below.
from scripts.distill_pubmed_keywords import (
    _SAFE_PARSER,
    _atomic_write_json,
    _coerce_to_bytes,
    _element_text,
)

logger = logging.getLogger(__name__)

_PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
DEFAULT_DIAGNOSE_CACHE_DIR: Final[Path] = (
    _PROJECT_ROOT / "data" / "bibentry" / "_diagnose_cache"
)
DEFAULT_EFETCH_BATCH: Final[int] = 50
# PubMed esearch returns at most 9,999 records per call — a hard NCBI
# cap. ``retstart > 9998`` is rejected, so pagination cannot bypass it
# (NCBI's docs point larger result sets to the EDirect CLI).
# https://www.ncbi.nlm.nih.gov/books/NBK25499/
PUBMED_ESEARCH_HARD_CAP: Final[int] = 9999

_QUOTED_TERM_RE: Final[re.Pattern[str]] = re.compile(r'"([^"]+)"')


# ---------------------------------------------------------------------------
# DATA CLASSES
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PaperMeta:
    pmid: str
    title: str
    journal: str
    year: str
    mesh: list[str]


@dataclass(slots=True)
class EsearchResult:
    """Outcome of one ``esearch`` call.

    ``total_count`` is the count NCBI reports for the query; when it
    exceeds ``len(pmids)`` the response was truncated by ``retmax`` and
    overlap percentages computed against ``pmids`` are not interpretable.
    """

    pmids: list[str]
    total_count: int

    @property
    def truncated(self) -> bool:
        return self.total_count > len(self.pmids)


@dataclass(slots=True)
class QueryRetrievalResult:
    """One labelled query + its PubMed PMIDs + per-PMID metadata.

    ``fetched_records`` is populated only for the PMIDs the report
    needs to display — typically a sample from the symmetric-difference
    set — so we don't burn efetch quota on the full result list.
    """

    label: str
    query: str
    pmids: list[str]
    fetched_records: dict[str, PaperMeta] = field(default_factory=dict)
    total_count: int = 0

    @property
    def truncated(self) -> bool:
        return self.total_count > len(self.pmids)


@dataclass(slots=True)
class OverlapStats:
    only_a: set[str]
    only_b: set[str]
    both: set[str]

    @property
    def total_a(self) -> int:
        return len(self.only_a) + len(self.both)

    @property
    def total_b(self) -> int:
        return len(self.only_b) + len(self.both)


# ---------------------------------------------------------------------------
# PURE HELPERS
# ---------------------------------------------------------------------------


def compute_overlap(a: list[str], b: list[str]) -> OverlapStats:
    """Set overlap of two PMID lists; order- and duplicate-insensitive."""
    set_a, set_b = set(a), set(b)
    return OverlapStats(
        only_a=set_a - set_b,
        only_b=set_b - set_a,
        both=set_a & set_b,
    )


def dedupe_substrings(phrases: list[str]) -> list[str]:
    """Drop phrases that are case-insensitive substrings of a longer kept one.

    Walks longest-first so the longest representative of any substring
    chain wins. Returns the kept entries in the original input order so
    callers that thread an LLR-sorted list through this function keep
    their ranking. Empty strings are dropped.
    """
    if not phrases:
        return []

    indexed = sorted(enumerate(phrases), key=lambda p: (-len(p[1]), p[0]))
    kept_lower: list[str] = []
    kept_indices: set[int] = set()
    for idx, phrase in indexed:
        lower = phrase.casefold()
        if not lower:
            continue
        if any(lower in longer for longer in kept_lower):
            continue
        kept_lower.append(lower)
        kept_indices.add(idx)
    return [p for i, p in enumerate(phrases) if i in kept_indices]


def _extract_quoted_terms(query: str) -> list[str]:
    return _QUOTED_TERM_RE.findall(query)


def _term_matches_meta(meta: PaperMeta, term: str) -> bool:
    """Case-insensitive substring match against title + MeSH headings.

    Not an exact replica of PubMed's tokenized matching — used only to
    annotate the diagnostic table with a plausible "which query term
    caught this paper" hint for human review.
    """
    haystack = meta.title.casefold() + " " + " ".join(meta.mesh).casefold()
    return term.casefold() in haystack


def _which_terms_match(
    meta: PaperMeta, terms: list[str], *, max_hits: int = 5
) -> list[str]:
    hits: list[str] = []
    for term in terms:
        if _term_matches_meta(meta, term):
            hits.append(term)
            if len(hits) >= max_hits:
                break
    return hits


# ---------------------------------------------------------------------------
# NCBI FETCHERS
# ---------------------------------------------------------------------------


def _cache_key(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()[:16]


def esearch_pmids(
    query: str,
    *,
    retmax: int = PUBMED_ESEARCH_HARD_CAP,
    mindate: str | None = None,
    maxdate: str | None = None,
    cache_dir: Path | None = None,
    use_cache: bool = True,
    fetcher: Callable[..., Any] | None = None,
) -> EsearchResult:
    """Submit ``query`` to NCBI esearch; return PMIDs + reported total count.

    Results are cached to ``cache_dir`` keyed by hash of
    ``(query, retmax, mindate, maxdate)`` so repeated runs don't burn
    NCBI quota. Pass ``use_cache=False`` to force a fresh fetch.

    ``fetcher`` is an injection point for tests: a callable taking
    esearch kwargs and returning a dict with keys ``IdList`` and
    optionally ``Count``. When the upstream ``Count`` exceeds
    ``retmax``, the returned ``EsearchResult.truncated`` is True and
    overlap percentages computed against this list are unreliable.
    """
    # Clamp before the cache lookup so retmax=15000 and retmax=100000
    # share a single cache entry (both fetch the same 9,999 PMIDs).
    if retmax > PUBMED_ESEARCH_HARD_CAP:
        logger.warning(
            f"PubMed esearch caps results at {PUBMED_ESEARCH_HARD_CAP} per "
            f"search; clamping requested retmax={retmax}. Narrow "
            "--diagnose-since to get full coverage of broader queries."
        )
        retmax = PUBMED_ESEARCH_HARD_CAP

    cache_dir = cache_dir or DEFAULT_DIAGNOSE_CACHE_DIR
    key = _cache_key(
        "esearch", query, str(retmax), mindate or "", maxdate or ""
    )
    cache_path = cache_dir / f"esearch_{key}.json"

    if use_cache and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            cached_total = (
                cached.get("total_count") if isinstance(cached, dict) else None
            )
            # Invalidate caches written by older versions that didn't
            # capture the upstream NCBI Count — without it we'd silently
            # report ``truncated=False`` for any truncated run.
            if (
                isinstance(cached, dict)
                and isinstance(cached.get("pmids"), list)
                and isinstance(cached_total, (int, str))
                and str(cached_total).isdigit()
            ):
                cached_pmids = [str(p) for p in cached["pmids"]]
                return EsearchResult(
                    pmids=cached_pmids,
                    total_count=int(cached_total),
                )
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            logger.warning(
                f"Corrupt esearch cache {cache_path.name}: {exc}; refetching"
            )

    kwargs: dict[str, Any] = {
        "db": "pubmed",
        "term": query,
        "retmax": str(retmax),
    }
    if mindate or maxdate:
        kwargs["datetype"] = "pdat"
    if mindate:
        kwargs["mindate"] = mindate
    if maxdate:
        kwargs["maxdate"] = maxdate

    if fetcher is None:
        fetcher = _default_esearch_fetcher

    record = fetcher(**kwargs)
    raw_ids: list[Any] = []
    upstream_count: int | None = None
    if isinstance(record, dict):
        ids = record.get("IdList", [])
        if isinstance(ids, list):
            raw_ids = ids
        count_val = record.get("Count")
        if isinstance(count_val, (int, str)) and str(count_val).isdigit():
            upstream_count = int(count_val)

    pmids: list[str] = []
    seen: set[str] = set()
    for pid in raw_ids:
        pid_str = str(pid)
        if pid_str and pid_str not in seen:
            seen.add(pid_str)
            pmids.append(pid_str)

    total_count = upstream_count if upstream_count is not None else len(pmids)
    if total_count > len(pmids):
        logger.warning(
            f"esearch retmax={retmax} truncated: NCBI reports {total_count} "
            f"matches, only the first {len(pmids)} are usable for overlap "
            "computation."
        )

    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(
            cache_path,
            {
                "query": query,
                "retmax": retmax,
                "mindate": mindate,
                "maxdate": maxdate,
                "pmids": pmids,
                "total_count": total_count,
            },
        )
    except OSError as exc:
        logger.warning(f"Could not write esearch cache {cache_path}: {exc}")

    return EsearchResult(pmids=pmids, total_count=total_count)


def _default_esearch_fetcher(**kwargs: Any) -> dict[str, Any]:
    from scripts.distill_pubmed_keywords import (
        _configure_entrez,
        _ncbi_retry,
        _ncbi_sleep,
    )

    resolved_key = _configure_entrez()
    from Bio import Entrez

    record = _ncbi_retry(
        Entrez.esearch,
        _reader=Entrez.read,
        **kwargs,
    )
    _ncbi_sleep(resolved_key)
    return dict(record) if record else {}


def efetch_metadata(
    pmids: list[str],
    *,
    batch_size: int = DEFAULT_EFETCH_BATCH,
    cache_dir: Path | None = None,
    use_cache: bool = True,
    fetcher: Callable[[list[str]], bytes] | None = None,
) -> dict[str, PaperMeta]:
    """Fetch title/journal/year/MeSH for each PMID; cache per-PMID.

    Per-PMID JSON cache files (``efetch_<pmid>.json``) are read first;
    PMIDs missing from cache are batched through NCBI efetch. Returns
    only the PMIDs that were either cached or successfully fetched —
    transient network failures show up as absences in the returned map
    so the caller can fall back to PMID-only display.
    """
    if not pmids:
        return {}
    cache_dir = cache_dir or DEFAULT_DIAGNOSE_CACHE_DIR

    out: dict[str, PaperMeta] = {}
    missing: list[str] = []
    for pmid in pmids:
        cache_path = cache_dir / f"efetch_{pmid}.json"
        if use_cache and cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if isinstance(cached, dict) and cached.get("pmid") == pmid:
                    mesh_raw = cached.get("mesh", [])
                    mesh_list = (
                        [str(m) for m in mesh_raw if m]
                        if isinstance(mesh_raw, list)
                        else []
                    )
                    out[pmid] = PaperMeta(
                        pmid=pmid,
                        title=str(cached.get("title", "")),
                        journal=str(cached.get("journal", "")),
                        year=str(cached.get("year", "")),
                        mesh=mesh_list,
                    )
                    continue
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning(f"Corrupt efetch cache for PMID {pmid}: {exc}")
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
            logger.warning(
                f"efetch batch {start}-{start + len(batch)} failed: {exc}"
            )
            continue
        coerced = _coerce_to_bytes(
            raw, f"efetch batch {start}-{start + len(batch)}"
        )
        if coerced is None:
            continue
        try:
            parsed = parse_pubmed_xml_for_meta(coerced)
        except etree.XMLSyntaxError as exc:
            logger.warning(
                f"efetch batch {start}-{start + len(batch)} XML parse error: {exc}"
            )
            continue
        for pmid in batch:
            meta = parsed.get(pmid)
            if meta is None:
                continue
            out[pmid] = meta
            try:
                _atomic_write_json(
                    cache_dir / f"efetch_{pmid}.json",
                    {
                        "pmid": pmid,
                        "title": meta.title,
                        "journal": meta.journal,
                        "year": meta.year,
                        "mesh": meta.mesh,
                    },
                )
            except OSError as exc:
                logger.warning(
                    f"Could not write efetch cache for PMID {pmid}: {exc}"
                )

    return out


def _default_efetch_fetcher(batch: list[str]) -> bytes:
    from scripts.distill_pubmed_keywords import (
        _configure_entrez,
        _ncbi_retry,
        _ncbi_sleep,
    )

    resolved_key = _configure_entrez()
    from Bio import Entrez

    raw = _ncbi_retry(
        Entrez.efetch,
        db="pubmed",
        id=",".join(batch),
        rettype="medline",
        retmode="xml",
        _reader=lambda h: h.read(),
    )
    _ncbi_sleep(resolved_key)
    if isinstance(raw, str):
        return raw.encode("utf-8")
    if isinstance(raw, bytes):
        return raw
    return bytes(raw)


def parse_pubmed_xml_for_meta(xml_bytes: bytes) -> dict[str, PaperMeta]:
    """Parse PubMed efetch XML into ``{pmid: PaperMeta}``.

    Raises ``etree.XMLSyntaxError`` on malformed XML.
    """
    root = etree.fromstring(xml_bytes, parser=_SAFE_PARSER)
    out: dict[str, PaperMeta] = {}
    for article in root.findall(".//PubmedArticle"):
        pmid = _element_text(article.find(".//MedlineCitation/PMID"))
        if not pmid:
            continue
        title = _element_text(article.find(".//ArticleTitle"))
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
        out[pmid] = PaperMeta(
            pmid=pmid, title=title, journal=journal, year=year, mesh=mesh
        )
    return out


# ---------------------------------------------------------------------------
# REPORT RENDERING
# ---------------------------------------------------------------------------


def _ascii_summary(overlap: OverlapStats, label_a: str, label_b: str) -> str:
    width = max(len(label_a), len(label_b)) + 6
    return (
        f"  {(label_a + ' only:').ljust(width)} {len(overlap.only_a):>6}\n"
        f"  {'Both:'.ljust(width)} {len(overlap.both):>6}\n"
        f"  {(label_b + ' only:').ljust(width)} {len(overlap.only_b):>6}\n"
    )


def _format_paper_table(
    pmids: list[str],
    records: dict[str, PaperMeta],
    *,
    annotate_terms: list[str] | None = None,
    title_truncate: int = 100,
) -> str:
    if not pmids:
        return "_(none)_\n"

    headers = ["PMID", "Title", "Journal", "Year", "MeSH (top 3)"]
    if annotate_terms is not None:
        headers.append("Matched query terms")

    rows: list[list[str]] = []
    for pmid in pmids:
        meta = records.get(pmid)
        if meta is None:
            row = [pmid, "_(metadata unavailable)_", "", "", ""]
            if annotate_terms is not None:
                row.append("")
            rows.append(row)
            continue
        title = meta.title
        if len(title) > title_truncate:
            title = title[: title_truncate - 1] + "…"
        title = title.replace("|", "\\|")
        journal = meta.journal.replace("|", "\\|")
        mesh_top = ", ".join(meta.mesh[:3]).replace("|", "\\|")
        row = [pmid, title, journal, meta.year, mesh_top]
        if annotate_terms is not None:
            matched = _which_terms_match(meta, annotate_terms)
            row.append(", ".join(matched) if matched else "_(no exact match)_")
        rows.append(row)

    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("|" + "|".join("---" for _ in headers) + "|")
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines) + "\n"


def render_diagnose_report(
    *,
    distilled: QueryRetrievalResult,
    production: QueryRetrievalResult,
    sample_size: int = 15,
    structural_issues: list[str] | None = None,
    diagnose_window: tuple[str | None, str | None] | None = None,
) -> str:
    """Render the Markdown comparison report."""
    overlap = compute_overlap(distilled.pmids, production.pmids)
    label_a = distilled.label
    label_b = production.label

    lines: list[str] = []
    timestamp = _dt.datetime.now().isoformat(timespec="seconds")
    lines.append(f"# PubMed query diagnostic — {timestamp}\n\n")

    if diagnose_window is not None:
        mindate, maxdate = diagnose_window
        lines.append(
            f"**Date window (pdat):** {mindate or '(any)'} → "
            f"{maxdate or '(any)'}. _PubMed's `pdat` is the assigned "
            "publication date in the index; the **Year** column below "
            "is the journal print year, which can differ for retroactively-"
            "indexed papers._\n\n"
        )

    lines.append(f"## Query A — {label_a}\n\n")
    lines.append("```\n" + distilled.query + "\n```\n\n")
    lines.append(f"## Query B — {label_b}\n\n")
    lines.append("```\n" + production.query + "\n```\n\n")

    truncated_any = distilled.truncated or production.truncated

    lines.append("## Result counts\n\n")
    if truncated_any:
        lines.append(
            "| Query | Returned PMIDs | NCBI Count | Truncated? |\n"
            "|---|---|---|---|\n"
        )
        for label, qres in ((label_a, distilled), (label_b, production)):
            flag = "⚠️ retmax cap hit" if qres.truncated else "no"
            lines.append(
                f"| {label} | {len(qres.pmids)} | {qres.total_count} | {flag} |\n"
            )
        lines.append(
            "\n> ⚠️ **One or more queries hit PubMed's 9,999-record "
            "cap** — overlap percentages below are computed against "
            "truncated PMID lists and are **not reliable** as global "
            "recall estimates. PubMed's esearch hard-limits results at "
            "9,999, so the fix is to narrow `--diagnose-since` to a "
            "window where both queries return ≤ 9,999, then compare "
            "those slices.\n\n"
        )
    else:
        lines.append("| Query | Total PMIDs |\n|---|---|\n")
        lines.append(f"| A — {label_a} | {overlap.total_a} |\n")
        lines.append(f"| B — {label_b} | {overlap.total_b} |\n\n")

    lines.append("## Overlap\n\n")
    lines.append("```\n" + _ascii_summary(overlap, label_a, label_b) + "```\n\n")

    sample_b_only = sorted(overlap.only_b)[:sample_size]
    sample_a_only = sorted(overlap.only_a)[:sample_size]
    production_terms = _extract_quoted_terms(production.query)
    distilled_terms = _extract_quoted_terms(distilled.query)

    lines.append(
        f"## Top {len(sample_b_only)} papers in {label_b} but not {label_a}\n\n"
        "**Recall-regression candidates.** Papers caught by the curated "
        "query but missed by the distilled one — inspect to see what "
        "terminology or synonyms the distilled query is missing.\n\n"
    )
    lines.append(
        _format_paper_table(
            sample_b_only,
            production.fetched_records,
            annotate_terms=production_terms,
        )
    )

    lines.append(
        f"\n## Top {len(sample_a_only)} papers in {label_a} but not {label_b}\n\n"
        "**Precision / discovery candidates.** Distilled finds these but "
        "production doesn't. Some may be true new finds (good), others may "
        "be off-topic noise (bad). Spot-check.\n\n"
    )
    lines.append(
        _format_paper_table(
            sample_a_only,
            distilled.fetched_records,
            annotate_terms=distilled_terms,
        )
    )

    if structural_issues:
        lines.append("\n## Known structural issues in the distill script\n\n")
        for issue in structural_issues:
            lines.append(f"- {issue}\n")

    lines.append(_interpretation_guide(overlap, truncated=truncated_any))

    return "".join(lines)


def _interpretation_guide(overlap: OverlapStats, *, truncated: bool = False) -> str:
    if overlap.total_a == 0 or overlap.total_b == 0:
        return (
            "\n## Interpretation\n\n"
            "_One query returned zero results — likely a syntax issue or "
            "date-range mismatch. Inspect both queries above before "
            "drawing conclusions._\n"
        )
    overlap_pct_b = 100 * len(overlap.both) / overlap.total_b
    coverage_line = (
        f"- The distilled query overlap covers **{overlap_pct_b:.1f}%** "
        "of papers returned by the production query"
        + (
            " (**unreliable** — truncation cap was hit; see counts table)."
            if truncated
            else "."
        )
    )
    return (
        "\n## Interpretation\n\n"
        f"{coverage_line}\n"
        "- If the **production-only** count is high relative to overlap, "
        "the distilled query is missing synonyms or canonical terms. The "
        "matched-terms column in that table shows which production terms "
        "caught each missed paper.\n"
        "- If the **distilled-only** count dwarfs both the overlap and "
        "the production-only set, the distilled query is likely broader "
        "and may be pulling in off-topic papers.\n"
        "- Substring dedup (`--dedupe-substrings`) does **not** change the "
        "PubMed result set materially — phrase-match semantics already "
        "collapse it — but it makes the query more readable.\n"
        "- Adding high-LLR unigrams + acronyms (`--include-unigrams N` / "
        "`--include-acronyms M`) is what materially changes recall vs "
        "production.\n"
    )


# ---------------------------------------------------------------------------
# END-TO-END DRIVER
# ---------------------------------------------------------------------------


def run_diagnose(
    *,
    distilled_query: str,
    production_query: str,
    distilled_label: str = "distilled",
    production_label: str = "SVD_QUERY",
    diagnose_since: str | None = None,
    diagnose_until: str | None = None,
    retmax: int | None = None,
    top_k: int = 15,
    structural_issues: list[str] | None = None,
    cache_dir: Path | None = None,
    esearch_fetcher: Callable[..., Any] | None = None,
    efetch_fetcher: Callable[[list[str]], bytes] | None = None,
) -> str:
    """End-to-end diagnostic: esearch both queries, sample diffs, render report.

    ``retmax=None`` uses ``PUBMED_ESEARCH_HARD_CAP``. Pass
    ``esearch_fetcher`` / ``efetch_fetcher`` to inject test doubles —
    the default fetchers hit NCBI via ``Bio.Entrez``.
    """
    effective_retmax = retmax if retmax is not None else PUBMED_ESEARCH_HARD_CAP
    distilled_result = esearch_pmids(
        distilled_query,
        retmax=effective_retmax,
        mindate=diagnose_since,
        maxdate=diagnose_until,
        cache_dir=cache_dir,
        fetcher=esearch_fetcher,
    )
    production_result = esearch_pmids(
        production_query,
        retmax=effective_retmax,
        mindate=diagnose_since,
        maxdate=diagnose_until,
        cache_dir=cache_dir,
        fetcher=esearch_fetcher,
    )

    overlap = compute_overlap(distilled_result.pmids, production_result.pmids)
    sample_a_only = sorted(overlap.only_a)[:top_k]
    sample_b_only = sorted(overlap.only_b)[:top_k]

    fetch_pmids = sorted(set(sample_a_only) | set(sample_b_only))
    metadata = efetch_metadata(
        fetch_pmids,
        cache_dir=cache_dir,
        fetcher=efetch_fetcher,
    )

    distilled = QueryRetrievalResult(
        label=distilled_label,
        query=distilled_query,
        pmids=distilled_result.pmids,
        fetched_records={
            p: metadata[p] for p in sample_a_only if p in metadata
        },
        total_count=distilled_result.total_count,
    )
    production = QueryRetrievalResult(
        label=production_label,
        query=production_query,
        pmids=production_result.pmids,
        fetched_records={
            p: metadata[p] for p in sample_b_only if p in metadata
        },
        total_count=production_result.total_count,
    )

    return render_diagnose_report(
        distilled=distilled,
        production=production,
        sample_size=top_k,
        structural_issues=structural_issues,
        diagnose_window=(diagnose_since, diagnose_until),
    )
