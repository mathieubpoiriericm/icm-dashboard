"""Unit tests for scripts/_query_diagnose.py.

Network-touching paths are exercised via the ``fetcher`` injection points on
``esearch_pmids`` / ``efetch_metadata`` so the suite runs fully offline.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from scripts._query_diagnose import (
    EsearchResult,
    OverlapStats,
    PaperMeta,
    QueryRetrievalResult,
    _ascii_summary,
    _extract_quoted_terms,
    _format_paper_table,
    _term_matches_meta,
    _which_terms_match,
    compute_overlap,
    dedupe_substrings,
    efetch_metadata,
    esearch_pmids,
    parse_pubmed_xml_for_meta,
    render_diagnose_report,
    run_diagnose,
)

# ---------------------------------------------------------------------------
# dedupe_substrings — substring collapse of T/A phrases
# ---------------------------------------------------------------------------


class TestDedupeSubstrings:
    def test_canonical_svd_case(self) -> None:
        out = dedupe_substrings(
            ["small vessel", "small vessel disease", "vessel disease"]
        )
        assert out == ["small vessel disease"]

    def test_empty_input(self) -> None:
        assert dedupe_substrings([]) == []

    def test_no_overlaps_preserves_all(self) -> None:
        phrases = ["alpha beta", "gamma delta", "epsilon zeta"]
        assert dedupe_substrings(phrases) == phrases

    def test_case_insensitive(self) -> None:
        out = dedupe_substrings(["Small Vessel", "small vessel disease"])
        assert out == ["small vessel disease"]

    def test_preserves_original_order(self) -> None:
        out = dedupe_substrings(
            ["one", "alpha beta gamma", "two", "delta epsilon"]
        )
        assert out == ["one", "alpha beta gamma", "two", "delta epsilon"]

    def test_idempotent(self) -> None:
        phrases = ["small vessel", "small vessel disease", "white matter"]
        once = dedupe_substrings(phrases)
        twice = dedupe_substrings(once)
        assert once == twice

    def test_drops_empty_strings(self) -> None:
        assert dedupe_substrings(["", "alpha", ""]) == ["alpha"]


# ---------------------------------------------------------------------------
# compute_overlap — PMID set math
# ---------------------------------------------------------------------------


class TestComputeOverlap:
    def test_basic(self) -> None:
        stats = compute_overlap(["1", "2", "3"], ["2", "3", "4"])
        assert stats.only_a == {"1"}
        assert stats.only_b == {"4"}
        assert stats.both == {"2", "3"}
        assert stats.total_a == 3
        assert stats.total_b == 3

    def test_empty_both(self) -> None:
        stats = compute_overlap([], [])
        assert stats.only_a == set()
        assert stats.only_b == set()
        assert stats.both == set()
        assert stats.total_a == 0
        assert stats.total_b == 0

    def test_empty_one_side(self) -> None:
        stats = compute_overlap(["1", "2"], [])
        assert stats.only_a == {"1", "2"}
        assert stats.only_b == set()
        assert stats.both == set()

    def test_duplicates_collapsed(self) -> None:
        stats = compute_overlap(["1", "1", "2"], ["2", "2", "3"])
        assert stats.total_a == 2
        assert stats.total_b == 2
        assert stats.both == {"2"}


# ---------------------------------------------------------------------------
# _extract_quoted_terms — pull "..." phrases out of Boolean queries
# ---------------------------------------------------------------------------


class TestExtractQuotedTerms:
    def test_simple(self) -> None:
        q = '"cerebral small vessel disease"[Title/Abstract]'
        assert _extract_quoted_terms(q) == ["cerebral small vessel disease"]

    def test_multiple(self) -> None:
        q = (
            '("small vessel"[Title/Abstract] OR "white matter"[Title/Abstract]) '
            'AND "stroke"[MeSH Terms]'
        )
        assert _extract_quoted_terms(q) == [
            "small vessel",
            "white matter",
            "stroke",
        ]

    def test_no_quotes(self) -> None:
        assert _extract_quoted_terms("plain text query") == []


# ---------------------------------------------------------------------------
# Term-match helpers
# ---------------------------------------------------------------------------


class TestTermMatching:
    def test_term_matches_meta_title(self) -> None:
        meta = PaperMeta(
            pmid="1", title="A study of CADASIL", journal="X", year="2024",
            mesh=[],
        )
        assert _term_matches_meta(meta, "cadasil")

    def test_term_matches_meta_mesh(self) -> None:
        meta = PaperMeta(
            pmid="1", title="X", journal="Y", year="2024",
            mesh=["Cerebral Small Vessel Diseases"],
        )
        assert _term_matches_meta(meta, "small vessel")

    def test_term_doesnt_match(self) -> None:
        meta = PaperMeta(
            pmid="1", title="Unrelated work", journal="Y", year="2024",
            mesh=["Heart Failure"],
        )
        assert not _term_matches_meta(meta, "small vessel")

    def test_which_terms_match_caps_at_max_hits(self) -> None:
        meta = PaperMeta(
            pmid="1",
            title="alpha beta gamma delta epsilon zeta eta",
            journal="X",
            year="2024",
            mesh=[],
        )
        terms = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta"]
        hits = _which_terms_match(meta, terms, max_hits=3)
        assert hits == ["alpha", "beta", "gamma"]


# ---------------------------------------------------------------------------
# parse_pubmed_xml_for_meta — PubMed efetch XML parser
# ---------------------------------------------------------------------------


_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_SIMPLE_PUBMED_XML = (_FIXTURES_DIR / "pubmed_efetch_sample.xml").read_bytes()


class TestParsePubmedXmlForMeta:
    def test_basic_extraction(self) -> None:
        out = parse_pubmed_xml_for_meta(_SIMPLE_PUBMED_XML)
        assert "99999" in out
        meta = out["99999"]
        assert meta.pmid == "99999"
        assert "test title" in meta.title.lower()
        assert "italics" in meta.title  # inline markup text gathered
        assert meta.journal == "Test Journal"
        assert meta.year == "2023"
        assert meta.mesh == ["Stroke", "White Matter"]

    def test_malformed_raises(self) -> None:
        from lxml import etree

        with pytest.raises(etree.XMLSyntaxError):
            parse_pubmed_xml_for_meta(b"<not-valid-xml")


# ---------------------------------------------------------------------------
# esearch_pmids — caching + fetcher injection
# ---------------------------------------------------------------------------


class TestEsearchPmids:
    def test_uses_injected_fetcher(self, tmp_path: Path) -> None:
        calls: list[dict] = []

        def fake_fetcher(**kwargs: object) -> dict:
            calls.append(dict(kwargs))
            return {"IdList": ["111", "222", "333"], "Count": "3"}

        result = esearch_pmids(
            "test query",
            cache_dir=tmp_path,
            fetcher=fake_fetcher,
        )
        assert result.pmids == ["111", "222", "333"]
        assert result.total_count == 3
        assert not result.truncated
        assert len(calls) == 1
        assert calls[0]["db"] == "pubmed"
        assert calls[0]["term"] == "test query"

    def test_caches_results(self, tmp_path: Path) -> None:
        calls: list[int] = []

        def fake_fetcher(**_: object) -> dict:
            calls.append(1)
            return {"IdList": ["1", "2"], "Count": "2"}

        first = esearch_pmids("q", cache_dir=tmp_path, fetcher=fake_fetcher)
        second = esearch_pmids("q", cache_dir=tmp_path, fetcher=fake_fetcher)
        assert first.pmids == second.pmids == ["1", "2"]
        assert first.total_count == second.total_count == 2
        # Second call should hit cache, no extra fetch.
        assert len(calls) == 1

    def test_use_cache_false_forces_refetch(self, tmp_path: Path) -> None:
        calls: list[int] = []

        def fake_fetcher(**_: object) -> dict:
            calls.append(1)
            return {"IdList": ["1"], "Count": "1"}

        esearch_pmids("q", cache_dir=tmp_path, fetcher=fake_fetcher)
        esearch_pmids(
            "q", cache_dir=tmp_path, fetcher=fake_fetcher, use_cache=False
        )
        assert len(calls) == 2

    def test_date_kwargs_threaded_through(self, tmp_path: Path) -> None:
        captured: dict = {}

        def fake_fetcher(**kwargs: object) -> dict:
            captured.update(kwargs)
            return {"IdList": [], "Count": "0"}

        esearch_pmids(
            "q",
            mindate="2020/01/01",
            maxdate="2024/12/31",
            cache_dir=tmp_path,
            fetcher=fake_fetcher,
        )
        assert captured["mindate"] == "2020/01/01"
        assert captured["maxdate"] == "2024/12/31"
        assert captured["datetype"] == "pdat"

    def test_dedupes_idlist(self, tmp_path: Path) -> None:
        def fake_fetcher(**_: object) -> dict:
            return {"IdList": ["1", "1", "2", "2", "3"], "Count": "5"}

        result = esearch_pmids("q", cache_dir=tmp_path, fetcher=fake_fetcher)
        assert result.pmids == ["1", "2", "3"]

    def test_truncation_detected_when_count_exceeds_returned(
        self, tmp_path: Path
    ) -> None:
        def fake_fetcher(**_: object) -> dict:
            # NCBI reports 50000 matches but the retmax=3 cap means
            # only 3 are in IdList — classic truncation case.
            return {"IdList": ["1", "2", "3"], "Count": "50000"}

        result = esearch_pmids(
            "q", retmax=3, cache_dir=tmp_path, fetcher=fake_fetcher
        )
        assert result.pmids == ["1", "2", "3"]
        assert result.total_count == 50000
        assert result.truncated

    def test_missing_count_falls_back_to_len(self, tmp_path: Path) -> None:
        def fake_fetcher(**_: object) -> dict:
            return {"IdList": ["1", "2"]}  # no Count field

        result = esearch_pmids("q", cache_dir=tmp_path, fetcher=fake_fetcher)
        assert result.total_count == 2
        assert not result.truncated

    def test_retmax_clamped_to_pubmed_cap(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        # PubMed esearch rejects retmax > 9999. We clamp + warn rather
        # than letting NCBI reject the call.
        captured: dict = {}

        def fake_fetcher(**kwargs: object) -> dict:
            captured.update(kwargs)
            return {"IdList": ["1", "2"], "Count": "2"}

        with caplog.at_level(logging.WARNING):
            esearch_pmids(
                "q", retmax=100000, cache_dir=tmp_path, fetcher=fake_fetcher
            )
        assert captured["retmax"] == "9999"
        assert any("clamping" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# efetch_metadata — caching + fetcher injection
# ---------------------------------------------------------------------------


class TestEfetchMetadata:
    def test_uses_injected_fetcher(self, tmp_path: Path) -> None:
        def fake_fetcher(batch: list[str]) -> bytes:
            return _SIMPLE_PUBMED_XML

        out = efetch_metadata(
            ["99999"], cache_dir=tmp_path, fetcher=fake_fetcher
        )
        assert "99999" in out
        assert out["99999"].journal == "Test Journal"

    def test_reads_cache(self, tmp_path: Path) -> None:
        calls: list[int] = []

        def fake_fetcher(batch: list[str]) -> bytes:
            calls.append(1)
            return _SIMPLE_PUBMED_XML

        first = efetch_metadata(
            ["99999"], cache_dir=tmp_path, fetcher=fake_fetcher
        )
        second = efetch_metadata(
            ["99999"], cache_dir=tmp_path, fetcher=fake_fetcher
        )
        assert first.keys() == second.keys()
        assert len(calls) == 1

    def test_empty_input_returns_empty(self, tmp_path: Path) -> None:
        out = efetch_metadata([], cache_dir=tmp_path)
        assert out == {}

    def test_missing_pmid_in_response_is_omitted(self, tmp_path: Path) -> None:
        def fake_fetcher(batch: list[str]) -> bytes:
            return _SIMPLE_PUBMED_XML  # returns PMID 99999 only

        out = efetch_metadata(
            ["99999", "11111"], cache_dir=tmp_path, fetcher=fake_fetcher
        )
        assert "99999" in out
        assert "11111" not in out

    def test_writes_cache_file_per_pmid(self, tmp_path: Path) -> None:
        def fake_fetcher(batch: list[str]) -> bytes:
            return _SIMPLE_PUBMED_XML

        efetch_metadata(
            ["99999"], cache_dir=tmp_path, fetcher=fake_fetcher
        )
        cache_path = tmp_path / "efetch_99999.json"
        assert cache_path.exists()
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        assert cached["pmid"] == "99999"
        assert cached["journal"] == "Test Journal"
        assert "Stroke" in cached["mesh"]


# ---------------------------------------------------------------------------
# Rendering — ASCII summary + paper tables + full report
# ---------------------------------------------------------------------------


class TestAsciiSummary:
    def test_includes_all_three_counts(self) -> None:
        stats = OverlapStats(
            only_a={"1", "2"}, only_b={"3", "4", "5"}, both={"6"}
        )
        out = _ascii_summary(stats, "A", "B")
        assert "A only:" in out
        assert "B only:" in out
        assert "Both:" in out
        assert "2" in out
        assert "3" in out
        assert "1" in out  # the "both" count


class TestFormatPaperTable:
    def test_empty(self) -> None:
        assert _format_paper_table([], {}) == "_(none)_\n"

    def test_basic_row(self) -> None:
        meta = PaperMeta(
            pmid="1",
            title="A short title",
            journal="Test J",
            year="2024",
            mesh=["Stroke", "Humans"],
        )
        out = _format_paper_table(["1"], {"1": meta})
        assert "| 1 |" in out
        assert "A short title" in out
        assert "Test J" in out
        assert "2024" in out
        assert "Stroke" in out

    def test_annotated_matched_terms(self) -> None:
        meta = PaperMeta(
            pmid="1", title="CADASIL review", journal="J", year="2024",
            mesh=["Stroke"],
        )
        out = _format_paper_table(
            ["1"], {"1": meta}, annotate_terms=["cadasil", "unrelated"]
        )
        assert "cadasil" in out
        assert "Matched query terms" in out

    def test_missing_metadata_falls_back_to_placeholder(self) -> None:
        out = _format_paper_table(["1"], {})
        assert "1" in out
        assert "metadata unavailable" in out

    def test_title_truncated(self) -> None:
        long_title = "x" * 200
        meta = PaperMeta(
            pmid="1", title=long_title, journal="J", year="2024", mesh=[]
        )
        out = _format_paper_table(["1"], {"1": meta}, title_truncate=20)
        assert "…" in out
        assert "x" * 200 not in out

    def test_pipe_in_title_is_escaped(self) -> None:
        meta = PaperMeta(
            pmid="1", title="Pipe | character", journal="J", year="2024",
            mesh=[],
        )
        out = _format_paper_table(["1"], {"1": meta})
        assert "Pipe \\|" in out


class TestRenderDiagnoseReport:
    def _build(self) -> tuple[QueryRetrievalResult, QueryRetrievalResult]:
        distilled = QueryRetrievalResult(
            label="distilled",
            query='"small vessel"[Title/Abstract]',
            pmids=["1", "2", "3", "4"],
            fetched_records={
                "4": PaperMeta(
                    pmid="4", title="Distilled-only paper",
                    journal="Test J", year="2024", mesh=["Stroke"],
                )
            },
        )
        production = QueryRetrievalResult(
            label="SVD_QUERY",
            query='"cerebral small vessel disease"[Title/Abstract]',
            pmids=["1", "2", "3", "5"],
            fetched_records={
                "5": PaperMeta(
                    pmid="5", title="Production-only paper",
                    journal="Test J", year="2024", mesh=["White Matter"],
                )
            },
        )
        return distilled, production

    def test_sections_present(self) -> None:
        distilled, production = self._build()
        out = render_diagnose_report(
            distilled=distilled,
            production=production,
            structural_issues=["Issue X"],
        )
        assert "PubMed query diagnostic" in out
        assert "## Query A" in out
        assert "## Query B" in out
        assert "## Result counts" in out
        assert "## Overlap" in out
        assert "## Top" in out  # the two top-K sections
        assert "Known structural issues" in out
        assert "Interpretation" in out
        assert "Issue X" in out

    def test_overlap_counts_in_report(self) -> None:
        distilled, production = self._build()
        out = render_diagnose_report(distilled=distilled, production=production)
        # Each side should report 4 total PMIDs.
        assert "| 4 |" in out

    def test_interpretation_warns_on_empty(self) -> None:
        distilled = QueryRetrievalResult(label="A", query="q", pmids=[])
        production = QueryRetrievalResult(label="B", query="q", pmids=["1"])
        out = render_diagnose_report(
            distilled=distilled, production=production
        )
        assert "zero results" in out


# ---------------------------------------------------------------------------
# run_diagnose — end-to-end driver
# ---------------------------------------------------------------------------


class TestRunDiagnose:
    def test_end_to_end_with_mocks(self, tmp_path: Path) -> None:
        esearch_calls: list[str] = []

        def fake_esearch(**kwargs: object) -> dict:
            term = str(kwargs.get("term", ""))
            esearch_calls.append(term)
            if "distilled" in term:
                return {"IdList": ["1", "2", "3"], "Count": "3"}
            return {"IdList": ["2", "3", "4"], "Count": "3"}

        def fake_efetch(batch: list[str]) -> bytes:
            return _SIMPLE_PUBMED_XML  # provides PMID 99999

        markdown = run_diagnose(
            distilled_query="distilled query",
            production_query="production query",
            top_k=5,
            cache_dir=tmp_path,
            esearch_fetcher=fake_esearch,
            efetch_fetcher=fake_efetch,
            structural_issues=["Test issue"],
        )
        # Both queries esearch'd
        assert len(esearch_calls) == 2
        assert "distilled query" in markdown
        assert "production query" in markdown
        assert "Test issue" in markdown
        assert "retmax cap hit" not in markdown
        assert "unreliable" not in markdown.lower()

    def test_truncation_surfaces_in_report(self, tmp_path: Path) -> None:
        def fake_esearch(**_: object) -> dict:
            # 5 returned but NCBI says there are 1_000_000 — truncation.
            return {"IdList": ["1", "2", "3", "4", "5"], "Count": "1000000"}

        def fake_efetch(batch: list[str]) -> bytes:
            return _SIMPLE_PUBMED_XML

        markdown = run_diagnose(
            distilled_query="dq",
            production_query="pq",
            top_k=2,
            cache_dir=tmp_path,
            esearch_fetcher=fake_esearch,
            efetch_fetcher=fake_efetch,
        )
        assert "Truncated?" in markdown
        assert "1000000" in markdown
        # Interpretation should warn that the overlap pct is unreliable.
        assert "unreliable" in markdown.lower()


class TestEsearchResult:
    def test_truncated_property(self) -> None:
        r = EsearchResult(pmids=["1", "2"], total_count=5)
        assert r.truncated

    def test_not_truncated_when_count_matches(self) -> None:
        r = EsearchResult(pmids=["1", "2"], total_count=2)
        assert not r.truncated
