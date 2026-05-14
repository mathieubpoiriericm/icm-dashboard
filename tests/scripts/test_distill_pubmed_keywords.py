"""Unit tests for scripts/distill_pubmed_keywords.py.

Network-touching paths are exercised via the ``fetcher`` injection
point on ``fetch_mesh_terms`` so the suite runs fully offline.
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import logging
import math
from collections import Counter
from pathlib import Path

import pytest
from scripts.distill_pubmed_keywords import (
    BaselineCounts,
    KeywordScore,
    MeshDescriptor,
    MeshQualifier,
    PaperText,
    _build_display_map,
    _foreground_acronyms,
    _foreground_counts_for,
    _llr_score,
    _rank_terms,
    aggregate_mesh,
    distill_keywords,
    fetch_mesh_terms,
    format_mesh_query,
    format_structured_query,
    format_titleabstract_query,
    load_baseline_cache,
    parse_pubmed_xml_for_mesh,
    stem_key,
)

_FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# stem_key — singular/plural collapse
# ---------------------------------------------------------------------------


class TestStemKey:
    def test_regular_plurals(self) -> None:
        assert stem_key("genes") == "gene"
        assert stem_key("markers") == "marker"
        assert stem_key("biomarkers") == "biomarker"
        assert stem_key("variants") == "variant"
        assert stem_key("vessels") == "vessel"

    def test_ies_to_y(self) -> None:
        assert stem_key("studies") == "study"
        assert stem_key("abnormalities") == "abnormality"
        assert stem_key("therapies") == "therapy"

    def test_irregular_plurals(self) -> None:
        assert stem_key("analyses") == "analysis"
        assert stem_key("diagnoses") == "diagnosis"
        assert stem_key("criteria") == "criterion"
        assert stem_key("data") == "datum"
        assert stem_key("hypotheses") == "hypothesis"

    def test_short_tokens_unchanged(self) -> None:
        assert stem_key("gene") == "gene"
        assert stem_key("mri") == "mri"
        assert stem_key("is") == "is"
        assert stem_key("at") == "at"

    def test_no_strip_suffixes_protected(self) -> None:
        # -ous / -us / -is / -ss must not lose their trailing -s.
        assert stem_key("nervous") == "nervous"
        assert stem_key("focus") == "focus"
        assert stem_key("various") == "various"
        assert stem_key("axis") == "axis"
        assert stem_key("stress") == "stress"
        assert stem_key("class") == "class"

    def test_case_normalised(self) -> None:
        assert stem_key("Genes") == "gene"
        assert stem_key("STUDIES") == "study"

    def test_hyphenated_token(self) -> None:
        # "follow-up" doesn't end in -s; should pass through.
        assert stem_key("follow-up") == "follow-up"


# ---------------------------------------------------------------------------
# Modal surface form
# ---------------------------------------------------------------------------


class TestModalSurface:
    def test_unigram_modal_surface(self) -> None:
        # Paper 1 has "gene" twice; paper 2 has "genes" once.
        # Aggregated under stem "gene"; the modal surface is "gene".
        papers = [
            [("gene", "gene"), ("gene", "gene"), ("gene", "genes")],
            [("gene", "genes")],
        ]
        display = _build_display_map(papers, 1)
        assert display[("gene",)] == "gene"

    def test_unigram_plural_wins_when_more_common(self) -> None:
        papers = [
            [("gene", "genes"), ("gene", "genes"), ("gene", "gene")],
        ]
        display = _build_display_map(papers, 1)
        assert display[("gene",)] == "genes"

    def test_bigram_surface_form(self) -> None:
        papers = [
            [("white", "white"), ("matter", "matter")],
            [("white", "white"), ("matter", "matter")],
        ]
        display = _build_display_map(papers, 2)
        assert display[("white", "matter")] == "white matter"


# ---------------------------------------------------------------------------
# LLR score
# ---------------------------------------------------------------------------


class TestLLRScore:
    def test_independent_term_low_llr(self) -> None:
        # Equal proportions in fg and bg → LLR ≈ 0 (after smoothing,
        # small but not exactly zero).
        llr = _llr_score(50, 50, 950, 950)
        assert llr < 1.0

    def test_distinctive_term_high_llr(self) -> None:
        # Term very over-represented in fg.
        llr = _llr_score(50, 1, 950, 9999)
        assert llr > 100.0

    def test_zero_bg_smoothing(self) -> None:
        # b=0 must not blow up via Laplace smoothing.
        llr = _llr_score(10, 0, 990, 100_000)
        assert math.isfinite(llr)
        assert llr > 10.0

    def test_known_value(self) -> None:
        # Hand-computed reference for a 2x2 table with smoothing
        # applied uniformly. Symmetric in cell ordering.
        a, b, c, d = 10, 5, 990, 9_995
        a_s, b_s, c_s, d_s = a + 0.5, b + 0.5, c + 0.5, d + 0.5
        n = a_s + b_s + c_s + d_s
        e_a = (a_s + b_s) * (a_s + c_s) / n
        e_b = (a_s + b_s) * (b_s + d_s) / n
        e_c = (c_s + d_s) * (a_s + c_s) / n
        e_d = (c_s + d_s) * (b_s + d_s) / n
        expected = 2 * (
            a_s * math.log(a_s / e_a)
            + b_s * math.log(b_s / e_b)
            + c_s * math.log(c_s / e_c)
            + d_s * math.log(d_s / e_d)
        )
        assert abs(_llr_score(a, b, c, d) - expected) < 1e-9


# ---------------------------------------------------------------------------
# Ranking with sign filter
# ---------------------------------------------------------------------------


class TestRankTermsLLR:
    def test_sign_filter_drops_baseline_dominant(self) -> None:
        # "common" is more frequent in baseline than foreground:
        #   fg rate = 10/1000 = 1% ; bg rate = 5000/10000 = 50% → drop.
        # "rare" is over-represented in foreground:
        #   fg rate = 990/1000 = 99% ; bg rate = 2/10000 ≈ 0.02% → keep.
        fg = Counter({("common",): 10, ("rare",): 990})
        df = Counter({("common",): 1, ("rare",): 5})
        bg = Counter({("common",): 5_000, ("rare",): 2})
        result = _rank_terms(
            fg, df, bg,
            total_fg=1_000, total_bg=10_000,
            min_df=1, top_n=10, min_llr=0.0,
        )
        terms = [r.term for r in result]
        assert "rare" in terms
        assert "common" not in terms

    def test_fallback_to_df_when_no_baseline(self) -> None:
        fg = Counter({("x",): 5, ("y",): 2})
        df = Counter({("x",): 3, ("y",): 2})
        result = _rank_terms(
            fg, df, None,
            total_fg=7, total_bg=None,
            min_df=1, top_n=10, min_llr=0.0,
        )
        assert result[0].term == "x"
        assert result[0].llr == 0.0  # no LLR computed in DF fallback

    def test_min_df_threshold(self) -> None:
        fg = Counter({("kept",): 3, ("dropped",): 1})
        df = Counter({("kept",): 2, ("dropped",): 1})
        bg = Counter({("kept",): 0, ("dropped",): 0})
        result = _rank_terms(
            fg, df, bg,
            total_fg=4, total_bg=1_000,
            min_df=2, top_n=10, min_llr=0.0,
        )
        terms = [r.term for r in result]
        assert "kept" in terms
        assert "dropped" not in terms

    def test_display_lookup_overrides_term_string(self) -> None:
        fg = Counter({("gene",): 3})
        df = Counter({("gene",): 2})
        bg = Counter({("gene",): 1})
        display = {("gene",): "GENES"}
        result = _rank_terms(
            fg, df, bg,
            total_fg=3, total_bg=1_000,
            min_df=1, top_n=10, min_llr=0.0,
            display=display,
        )
        assert result[0].term == "GENES"


# ---------------------------------------------------------------------------
# MeSH XML parsing
# ---------------------------------------------------------------------------


class TestMeshParse:
    def test_parse_descriptors_and_qualifiers(self) -> None:
        xml = _FIXTURES.joinpath("mesh_sample.xml").read_bytes()
        parsed = parse_pubmed_xml_for_mesh(xml)
        assert set(parsed.keys()) == {"15905468", "23649698", "99999999"}

        d_15905468 = parsed["15905468"]
        terms = [d.term for d in d_15905468]
        assert "Cerebral Small Vessel Diseases" in terms
        assert "Intercellular Adhesion Molecule-1" in terms

    def test_major_flag_extracted(self) -> None:
        xml = _FIXTURES.joinpath("mesh_sample.xml").read_bytes()
        parsed = parse_pubmed_xml_for_mesh(xml)
        for d in parsed["15905468"]:
            if d.term == "Cerebral Small Vessel Diseases":
                assert d.major is True
            if d.term == "Magnetic Resonance Imaging":
                assert d.major is False

    def test_qualifiers_extracted(self) -> None:
        xml = _FIXTURES.joinpath("mesh_sample.xml").read_bytes()
        parsed = parse_pubmed_xml_for_mesh(xml)
        for d in parsed["15905468"]:
            if d.term == "Cerebrovascular Disorders":
                assert len(d.qualifiers) == 1
                assert d.qualifiers[0].term == "etiology"
                assert d.qualifiers[0].major is True

    def test_article_without_mesh_returns_empty_list(self) -> None:
        xml = _FIXTURES.joinpath("mesh_sample.xml").read_bytes()
        parsed = parse_pubmed_xml_for_mesh(xml)
        assert parsed["99999999"] == []


# ---------------------------------------------------------------------------
# MeSH aggregation (major topics get 2x weight)
# ---------------------------------------------------------------------------


class TestAggregateMesh:
    def test_major_outweighs_minor_on_tie(self) -> None:
        # Two descriptors each appear in 2 papers (same DF). The one
        # tagged Major in both papers should sort first due to 2x weight.
        pmid_to_descriptors = {
            "p1": [
                MeshDescriptor(term="X", ui="D1", major=True),
                MeshDescriptor(term="Y", ui="D2", major=False),
            ],
            "p2": [
                MeshDescriptor(term="X", ui="D1", major=True),
                MeshDescriptor(term="Y", ui="D2", major=False),
            ],
        }
        result = aggregate_mesh(pmid_to_descriptors, top_n=10)
        assert result[0].term == "X"
        assert result[0].total_count == 4  # 2 + 2 (major 2x)
        assert result[1].term == "Y"
        assert result[1].total_count == 2  # 1 + 1

    def test_document_frequency_dominates_total_count(self) -> None:
        # Z appears in 3 papers as minor → DF=3, weight=3.
        # W appears in 2 papers as major → DF=2, weight=4.
        # DF dominates → Z ranks first.
        pmid_to_descriptors = {
            "p1": [MeshDescriptor(term="Z", ui="D3", major=False)],
            "p2": [MeshDescriptor(term="Z", ui="D3", major=False),
                   MeshDescriptor(term="W", ui="D4", major=True)],
            "p3": [MeshDescriptor(term="Z", ui="D3", major=False),
                   MeshDescriptor(term="W", ui="D4", major=True)],
        }
        result = aggregate_mesh(pmid_to_descriptors, top_n=10)
        assert result[0].term == "Z"
        assert result[0].document_frequency == 3
        assert result[1].term == "W"


# ---------------------------------------------------------------------------
# Boolean query formatting
# ---------------------------------------------------------------------------


class TestBuildQuery:
    def test_titleabstract_query(self) -> None:
        scores = [
            KeywordScore(term="white matter", document_frequency=5, total_count=5),
            KeywordScore(term="microbleeds", document_frequency=4, total_count=4),
        ]
        q = format_titleabstract_query(scores, top=2)
        assert q == '"white matter"[Title/Abstract] OR "microbleeds"[Title/Abstract]'

    def test_mesh_query(self) -> None:
        scores = [
            KeywordScore(
                term="Cerebral Small Vessel Diseases",
                document_frequency=10,
                total_count=10,
            )
        ]
        q = format_mesh_query(scores, top=1)
        assert q == '"Cerebral Small Vessel Diseases"[MeSH Terms]'

    def test_structured_query_combines_with_AND(self) -> None:
        mesh = [KeywordScore(term="Brain", document_frequency=5, total_count=5)]
        phrases = [
            KeywordScore(term="white matter", document_frequency=4, total_count=4)
        ]
        q = format_structured_query(mesh, phrases, mesh_top=1, phrase_top=1)
        assert q.startswith("(")
        assert "[MeSH Terms]" in q
        assert " AND " in q
        assert "[Title/Abstract]" in q

    def test_structured_query_mesh_only_when_phrases_empty(self) -> None:
        mesh = [KeywordScore(term="Brain", document_frequency=5, total_count=5)]
        q = format_structured_query(mesh, [], mesh_top=1, phrase_top=10)
        assert q == '("Brain"[MeSH Terms])'

    def test_structured_query_returns_empty_when_no_input(self) -> None:
        assert format_structured_query([], [], mesh_top=10, phrase_top=10) == ""


# ---------------------------------------------------------------------------
# Baseline cache I/O
# ---------------------------------------------------------------------------


def _write_baseline_payload(
    path: Path, *, built_at: str, override: dict | None = None
) -> None:
    payload = {
        "schema_version": 1,
        "built_at": built_at,
        "params": {"size_requested": 100},
        "total_docs": 10,
        "total_unigrams": 8,
        "total_bigrams": 2,
        "total_trigrams": 0,
        "total_acronyms": 4,
        "unigrams": {"gene": 5, "variant": 3},
        "bigrams": {"white matter": 2},
        "trigrams": {},
        "acronyms": {"MRI": 4},
    }
    if override:
        payload.update(override)
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as gz:
        json.dump(payload, gz)


class TestBaselineCache:
    def test_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "baseline.json.gz"
        _write_baseline_payload(
            path, built_at=dt.datetime.now(dt.UTC).isoformat()
        )
        bc = load_baseline_cache(path)
        assert bc.total_docs == 10
        # Unigrams stored flat in JSON; loaded as `(stem,)` tuples to
        # match foreground ranking keys.
        assert bc.unigrams[("gene",)] == 5
        assert bc.unigrams[("variant",)] == 3
        assert bc.bigrams[("white", "matter")] == 2
        assert bc.acronyms["MRI"] == 4
        assert bc.total_unigrams == 8

    def test_missing_cache_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_baseline_cache(tmp_path / "does-not-exist.json.gz")

    def test_schema_mismatch_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "baseline.json.gz"
        _write_baseline_payload(
            path,
            built_at=dt.datetime.now(dt.UTC).isoformat(),
            override={"schema_version": 9999},
        )
        with pytest.raises(RuntimeError, match="schema"):
            load_baseline_cache(path)

    def test_stale_logs_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        path = tmp_path / "baseline.json.gz"
        old = (dt.datetime.now(dt.UTC) - dt.timedelta(days=400)).isoformat()
        _write_baseline_payload(path, built_at=old)
        with caplog.at_level(logging.WARNING):
            load_baseline_cache(path)
        assert any("days old" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# MeSH cache (idempotency)
# ---------------------------------------------------------------------------


class TestMeshCache:
    def test_idempotent_when_all_cached(self, tmp_path: Path) -> None:
        # Pre-populate cache for both PMIDs.
        for pmid, term in (("111", "Brain"), ("222", "Stroke")):
            (tmp_path / f"{pmid}.json").write_text(
                json.dumps(
                    {
                        "pmid": pmid,
                        "descriptors": [
                            {"term": term, "ui": "D0", "major": False, "qualifiers": []}
                        ],
                        "fetched_at": "2025-01-01T00:00:00",
                    }
                )
            )

        calls: list[list[str]] = []

        def stub_fetcher(batch: list[str]) -> bytes:
            calls.append(batch)
            return b""

        result = fetch_mesh_terms(
            ["111", "222"], cache_dir=tmp_path, fetcher=stub_fetcher
        )

        assert calls == []  # zero network calls
        assert result["111"][0].term == "Brain"
        assert result["222"][0].term == "Stroke"

    def test_fetches_missing_and_writes_cache(self, tmp_path: Path) -> None:
        fixture = _FIXTURES.joinpath("mesh_sample.xml").read_bytes()

        def stub_fetcher(batch: list[str]) -> bytes:
            return fixture

        result = fetch_mesh_terms(
            ["15905468", "23649698"],
            cache_dir=tmp_path,
            fetcher=stub_fetcher,
        )

        assert "15905468" in result
        assert "23649698" in result
        # Cache files written.
        assert (tmp_path / "15905468.json").exists()
        assert (tmp_path / "23649698.json").exists()
        # Second invocation reads from cache without calling stub.
        second_calls: list[list[str]] = []

        def second_stub(batch: list[str]) -> bytes:
            second_calls.append(batch)
            return b""

        fetch_mesh_terms(
            ["15905468", "23649698"],
            cache_dir=tmp_path,
            fetcher=second_stub,
        )
        assert second_calls == []


# ---------------------------------------------------------------------------
# Foreground counting helpers
# ---------------------------------------------------------------------------


class TestForegroundCounts:
    def test_ngram_doc_frequency_versus_total_count(self) -> None:
        # Paper 1: "gene gene marker" → "(gene,)" appears 2x, "(marker,)" once
        # Paper 2: "gene marker"      → "(gene,)" once, "(marker,)" once
        pairs = [
            [("gene", "gene"), ("gene", "gene"), ("marker", "marker")],
            [("gene", "gene"), ("marker", "marker")],
        ]
        tf, df = _foreground_counts_for(pairs, n=1, filter_content=True)
        assert tf[("gene",)] == 3
        assert df[("gene",)] == 2  # appears in 2 distinct papers
        assert tf[("marker",)] == 2
        assert df[("marker",)] == 2

    def test_stopword_in_ngram_disqualifies(self) -> None:
        # "the" is a stopword; the bigram "the gene" should be excluded
        # from both counters when filter_content=True.
        pairs = [
            [("the", "the"), ("gene", "gene"), ("variant", "variant")],
        ]
        tf, df = _foreground_counts_for(pairs, n=2, filter_content=True)
        assert ("the", "gene") not in tf
        assert ("gene", "variant") in tf

    def test_acronym_detection(self) -> None:
        papers = [
            PaperText(pmid="1", title="WMH and CADASIL", abstract="MRI"),
            PaperText(pmid="2", title="WMH study", abstract="MRI and SVD"),
        ]
        tf, df = _foreground_acronyms(papers)
        assert tf["WMH"] == 2
        assert df["WMH"] == 2
        assert tf["MRI"] == 2
        assert tf["CADASIL"] == 1


# ---------------------------------------------------------------------------
# End-to-end distillation (no network, baseline injected directly)
# ---------------------------------------------------------------------------


class TestDistillKeywords:
    def test_runs_with_baseline_and_no_mesh(self) -> None:
        papers = [
            PaperText(
                pmid="111",
                title="cerebral microbleeds and white matter hyperintensities",
                abstract="MRI confirmed white matter hyperintensities and "
                         "cerebral microbleeds in patients with SVD.",
            ),
            PaperText(
                pmid="222",
                title="white matter hyperintensities in stroke",
                abstract="WMH on MRI in stroke patients.",
            ),
        ]
        # Inject a synthetic baseline with nothing in common with the
        # cSVD-distinctive terms — every term in the foreground should
        # then be ranked as highly distinctive.
        baseline = BaselineCounts(
            schema_version=1,
            built_at=dt.datetime.now(dt.UTC).isoformat(),
            params={},
            total_docs=1_000,
            unigrams=Counter({("unrelated",): 5_000}),
            bigrams=Counter({("foo", "bar"): 500}),
            trigrams=Counter({("foo", "bar", "baz"): 50}),
            acronyms=Counter({"FOO": 200}),
            total_unigrams=100_000,
            total_bigrams=10_000,
            total_trigrams=1_000,
            total_acronyms=500,
        )
        result = distill_keywords(
            papers,
            baseline=baseline,
            top_n=5,
            min_df=1,
            min_llr=0.0,
            mesh_descriptors=None,
        )
        assert result.papers == 2
        bigram_terms = [s.term for s in result.bigrams]
        assert "white matter" in bigram_terms
        # Acronyms surfaced with surface form.
        acronyms = [s.term for s in result.acronyms]
        assert "WMH" in acronyms or "MRI" in acronyms

    def test_mesh_terms_flow_through(self) -> None:
        papers = [
            PaperText(pmid="111", title="t", abstract="a"),
        ]
        baseline = BaselineCounts(
            schema_version=1,
            built_at=dt.datetime.now(dt.UTC).isoformat(),
            params={},
            total_docs=1,
            unigrams=Counter(),
            bigrams=Counter(),
            trigrams=Counter(),
            acronyms=Counter(),
            total_unigrams=1,
            total_bigrams=1,
            total_trigrams=1,
            total_acronyms=1,
        )
        mesh = {
            "111": [
                MeshDescriptor(term="Brain", ui="D1", major=True),
                MeshDescriptor(term="Stroke", ui="D2", major=False),
            ]
        }
        result = distill_keywords(
            papers,
            baseline=baseline,
            min_df=1,
            min_llr=0.0,
            mesh_descriptors=mesh,
            mesh_top=5,
        )
        terms = [s.term for s in result.mesh_terms]
        assert "Brain" in terms
        assert "Stroke" in terms


# ---------------------------------------------------------------------------
# Foreground accumulator + KeywordScore (sanity)
# ---------------------------------------------------------------------------


def test_keyword_score_default_llr_zero() -> None:
    s = KeywordScore(term="x", document_frequency=1, total_count=1)
    assert s.llr == 0.0


def test_mesh_qualifier_dataclass() -> None:
    q = MeshQualifier(term="etiology", ui="Q1", major=True)
    assert q.major is True
