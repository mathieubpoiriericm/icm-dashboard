"""Unit tests for scripts/distill_pubmed_keywords.py.

Network-touching paths are exercised via the ``fetcher`` injection
point on ``fetch_mesh_terms`` and ``fetch_fulltext_batch`` so the
suite runs fully offline.
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
    FULLTEXT_SCHEMA_VERSION,
    BaselineCounts,
    FulltextRecord,
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
    fetch_fulltext_batch,
    fetch_mesh_terms,
    format_mesh_query,
    format_structured_query,
    format_titleabstract_query,
    load_baseline_cache,
    main,
    parse_jats_for_sections,
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

    def test_malformed_xml_raises(self) -> None:
        # Invariant: malformed XML must surface so fetch_mesh_terms skips
        # the batch instead of caching empty descriptors for every PMID.
        from lxml import etree

        with pytest.raises(etree.XMLSyntaxError):
            parse_pubmed_xml_for_mesh(b"<not-xml")


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

    def test_plural_section_label_filtered_after_stemming(self) -> None:
        # Regression: "results"/"methods" stem to "result"/"method" which
        # aren't in the stopword set on their own. The filter must also
        # check the surface form so structured-abstract section labels
        # don't leak through stemming.
        pairs = [
            [("result", "results"), ("gene", "gene")],
            [("method", "methods"), ("gene", "gene")],
            [("intervention", "interventions"), ("gene", "gene")],
            [("measurement", "measurements"), ("gene", "gene")],
        ]
        tf, _ = _foreground_counts_for(pairs, n=1, filter_content=True)
        assert ("result",) not in tf
        assert ("method",) not in tf
        assert ("intervention",) not in tf
        assert ("measurement",) not in tf
        assert ("gene",) in tf

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


# ---------------------------------------------------------------------------
# parse_jats_for_sections — IMRaD extraction from JATS XML
# ---------------------------------------------------------------------------


class TestParseJatsForSections:
    def test_extracts_all_imrad_sections(self) -> None:
        xml_bytes = (_FIXTURES / "jats_sample.xml").read_bytes()
        sections = parse_jats_for_sections(xml_bytes)
        assert set(sections.keys()) == {
            "introduction",
            "methods",
            "results",
            "discussion",
        }
        assert "small vessel disease" in sections["introduction"].lower()
        assert "magnetic resonance imaging" in sections["methods"].lower()
        assert "intercellular adhesion molecule" in sections["results"].lower()
        assert "prospective design" in sections["discussion"].lower()

    def test_handles_synonym_titles(self) -> None:
        xml_bytes = (_FIXTURES / "jats_synonym_titles.xml").read_bytes()
        sections = parse_jats_for_sections(xml_bytes)
        # "Background" maps to introduction.
        assert "subarachnoid haemorrhage" in sections["introduction"].lower()
        # "Subjects and Methods" + "Experimental Procedures" both map to
        # methods and are joined with a blank line.
        assert "diffusion tensor imaging" in sections["methods"].lower()
        assert "haematoxylin" in sections["methods"].lower()
        # "Findings" maps to results.
        assert "fractional anisotropy" in sections["results"].lower()
        # "Conclusions" maps to discussion.
        assert "subclinical white matter injury" in sections["discussion"].lower()

    def test_skips_unlabeled_sections(self) -> None:
        # The sample fixture has an Acknowledgments section without
        # sec-type and an unlabeled References section. Neither should
        # appear in any IMRaD bucket.
        xml_bytes = (_FIXTURES / "jats_sample.xml").read_bytes()
        sections = parse_jats_for_sections(xml_bytes)
        combined = " ".join(sections.values()).lower()
        assert "austrian stroke prevention study participants" not in combined
        assert "this section should be dropped by the parser" not in combined

    def test_concatenates_duplicate_sectypes(self) -> None:
        # jats_sample.xml has two <sec sec-type="methods"> blocks
        # ("Materials and Methods" and "Statistical Methods"). Both
        # should contribute and be joined with a blank line.
        xml_bytes = (_FIXTURES / "jats_sample.xml").read_bytes()
        sections = parse_jats_for_sections(xml_bytes)
        methods = sections["methods"]
        # Content from the first methods block.
        assert "elisa" in methods.lower()
        # Content from the second methods block.
        assert "stata" in methods.lower()
        # The two distinct paragraph runs must be separated by a blank line.
        assert "\n\n" in methods

    def test_returns_four_keys_even_when_empty(self) -> None:
        # JATS-shaped XML with a body but no <sec>: every label is "".
        xml = b"<article><body></body></article>"
        sections = parse_jats_for_sections(xml)
        assert sections == {
            "introduction": "",
            "methods": "",
            "results": "",
            "discussion": "",
        }

    def test_raises_on_malformed_xml(self) -> None:
        # Caller relies on XMLSyntaxError to distinguish parse failures
        # (skip cache, retry) from empty bodies (cache as "none").
        from lxml import etree  # type: ignore[import-untyped]

        with pytest.raises(etree.XMLSyntaxError):
            parse_jats_for_sections(b"<not-xml>this isn't")


# ---------------------------------------------------------------------------
# fetch_fulltext_batch — cache + injected fetchers
# ---------------------------------------------------------------------------


def _write_cached_record(cache_dir: Path, record: FulltextRecord) -> None:
    """Helper: write a JSON file that `_read_fulltext_cache` will accept."""
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
        "fetched_at": dt.datetime.now(dt.UTC).isoformat(),
    }
    (cache_dir / f"{record.pmid}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


class TestFetchFulltextBatch:
    def test_reads_cache_without_invoking_fetcher(self, tmp_path: Path) -> None:
        _write_cached_record(
            tmp_path,
            FulltextRecord(
                pmid="111",
                pmcid="PMC1",
                introduction="cached intro",
                methods="cached methods",
                results="cached results",
                discussion="cached discussion",
            ),
        )

        elink_calls: list[list[str]] = []
        efetch_calls: list[str] = []

        def fail_elink(batch: list[str]) -> dict[str, str | None]:
            elink_calls.append(batch)
            raise AssertionError("fetcher should not be called on cache hit")

        def fail_efetch(_pmcid: str) -> bytes | None:
            efetch_calls.append(_pmcid)
            raise AssertionError("fetcher should not be called on cache hit")

        out = fetch_fulltext_batch(
            ["111"],
            tmp_path,
            fetcher_elink=fail_elink,
            fetcher_efetch=fail_efetch,
        )
        assert "111" in out
        assert out["111"].introduction == "cached intro"
        assert elink_calls == []
        assert efetch_calls == []

    def test_writes_cache_on_successful_fetch(self, tmp_path: Path) -> None:
        jats_bytes = (_FIXTURES / "jats_sample.xml").read_bytes()

        def stub_elink(batch: list[str]) -> dict[str, str | None]:
            return dict.fromkeys(batch, "PMC1234567")

        def stub_efetch(_pmcid: str) -> bytes | None:
            return jats_bytes

        out = fetch_fulltext_batch(
            ["15905468"],
            tmp_path,
            fetcher_elink=stub_elink,
            fetcher_efetch=stub_efetch,
        )
        assert out["15905468"].pmcid == "PMC1234567"
        assert "small vessel disease" in out["15905468"].introduction.lower()

        cache_path = tmp_path / "15905468.json"
        assert cache_path.exists()
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        assert payload["schema_version"] == FULLTEXT_SCHEMA_VERSION
        assert payload["pmid"] == "15905468"
        assert payload["pmcid"] == "PMC1234567"
        assert "small vessel disease" in payload["sections"]["introduction"].lower()

    def test_caches_negative_result_when_no_pmc(self, tmp_path: Path) -> None:
        # When elink returns no PMCID for the PMID, we cache a sentinel
        # (pmcid=None) so subsequent runs don't re-elink the same PMID.
        elink_calls: list[list[str]] = []
        efetch_calls: list[str] = []

        def stub_elink(batch: list[str]) -> dict[str, str | None]:
            elink_calls.append(list(batch))
            return dict.fromkeys(batch)

        def stub_efetch(_pmcid: str) -> bytes | None:
            efetch_calls.append(_pmcid)
            raise AssertionError("efetch must not run when elink returned None")

        first = fetch_fulltext_batch(
            ["999"],
            tmp_path,
            fetcher_elink=stub_elink,
            fetcher_efetch=stub_efetch,
        )
        assert first["999"].pmcid is None
        assert first["999"].introduction == ""
        assert (tmp_path / "999.json").exists()
        assert elink_calls == [["999"]]

        # Second invocation hits the negative cache, so elink isn't
        # called a second time.
        second = fetch_fulltext_batch(
            ["999"],
            tmp_path,
            fetcher_elink=stub_elink,
            fetcher_efetch=stub_efetch,
        )
        assert second["999"].pmcid is None
        assert elink_calls == [["999"]]  # unchanged
        assert efetch_calls == []

    def test_skips_on_fetcher_error(self, tmp_path: Path) -> None:
        def broken_elink(_batch: list[str]) -> dict[str, str | None]:
            raise RuntimeError("simulated NCBI failure")

        def unused_efetch(_pmcid: str) -> bytes | None:
            raise AssertionError("efetch should not run when elink failed")

        out = fetch_fulltext_batch(
            ["123"],
            tmp_path,
            fetcher_elink=broken_elink,
            fetcher_efetch=unused_efetch,
        )
        # No cache file written; PMID missing from output map.
        assert "123" not in out
        assert not (tmp_path / "123.json").exists()

    def test_skips_cache_write_on_xml_parse_error(self, tmp_path: Path) -> None:
        def stub_elink(batch: list[str]) -> dict[str, str | None]:
            return dict.fromkeys(batch, "PMC777")

        def malformed_efetch(_pmcid: str) -> bytes | None:
            return b"<not-jats>truncated"

        out = fetch_fulltext_batch(
            ["555"],
            tmp_path,
            fetcher_elink=stub_elink,
            fetcher_efetch=malformed_efetch,
        )
        # Same reasoning as the MeSH batch's parse-error handler: don't
        # poison the cache with empty sections.
        assert "555" not in out
        assert not (tmp_path / "555.json").exists()

    def test_batches_elink_for_multiple_missing_pmids(
        self, tmp_path: Path
    ) -> None:
        # The whole point of batching is one elink call per
        # fetch_fulltext_batch invocation, regardless of how many PMIDs
        # are missing from cache.
        jats_bytes = (_FIXTURES / "jats_sample.xml").read_bytes()
        elink_calls: list[list[str]] = []

        def stub_elink(batch: list[str]) -> dict[str, str | None]:
            elink_calls.append(list(batch))
            # Only the first PMID resolves to PMC; second has no mirror.
            return {batch[0]: "PMC1", batch[1]: None}

        def stub_efetch(_pmcid: str) -> bytes | None:
            return jats_bytes

        out = fetch_fulltext_batch(
            ["111", "222"],
            tmp_path,
            fetcher_elink=stub_elink,
            fetcher_efetch=stub_efetch,
        )
        assert elink_calls == [["111", "222"]]
        assert out["111"].pmcid == "PMC1"
        assert out["222"].pmcid is None

    def test_refetches_on_schema_mismatch(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Stale cache claims schema_version=0; the loader must warn and
        # treat the entry as missing so the fetcher is invoked.
        (tmp_path / "777.json").write_text(
            json.dumps(
                {
                    "schema_version": 0,
                    "pmid": "777",
                    "pmcid": "PMC777",
                    "sections": {"introduction": "stale"},
                    "fetched_at": "2020-01-01T00:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )

        jats_bytes = (_FIXTURES / "jats_sample.xml").read_bytes()
        elink_calls: list[list[str]] = []

        def stub_elink(batch: list[str]) -> dict[str, str | None]:
            elink_calls.append(list(batch))
            return dict.fromkeys(batch, "PMC777")

        def stub_efetch(_pmcid: str) -> bytes | None:
            return jats_bytes

        with caplog.at_level(logging.WARNING):
            out = fetch_fulltext_batch(
                ["777"],
                tmp_path,
                fetcher_elink=stub_elink,
                fetcher_efetch=stub_efetch,
            )
        assert elink_calls == [["777"]]
        assert out["777"].pmcid == "PMC777"
        assert "schema mismatch" in caplog.text.lower()


# ---------------------------------------------------------------------------
# PaperText.fulltext + FulltextRecord.as_text
# ---------------------------------------------------------------------------


def test_paper_text_combined_includes_fulltext() -> None:
    paper = PaperText(
        pmid="1",
        title="A study of vessels",
        abstract="We measured things.",
        fulltext="Body text covering Methods and Results.",
    )
    assert paper.combined == (
        "A study of vessels We measured things. "
        "Body text covering Methods and Results."
    )


def test_paper_text_combined_omits_empty_fulltext() -> None:
    # Pre-fulltext callers (and papers without PMC) should produce the
    # same combined string as the v1 script — no trailing spaces.
    paper = PaperText(pmid="1", title="Title", abstract="Abstract.")
    assert paper.combined == "Title Abstract."


def test_fulltext_record_as_text_orders_imrad() -> None:
    # Pass kwargs out of IMRaD order; as_text must still emit them in
    # Introduction -> Methods -> Results -> Discussion.
    record = FulltextRecord(
        pmid="1",
        pmcid="PMC1",
        discussion="D-text",
        results="R-text",
        methods="M-text",
        introduction="I-text",
    )
    assert record.as_text() == "I-text\n\nM-text\n\nR-text\n\nD-text"


def test_fulltext_record_as_text_skips_empty_sections() -> None:
    record = FulltextRecord(
        pmid="1",
        pmcid="PMC1",
        introduction="intro only",
        discussion="discussion only",
    )
    assert record.as_text() == "intro only\n\ndiscussion only"


# ---------------------------------------------------------------------------
# main() — CLI flag wiring for --no-fulltext
# ---------------------------------------------------------------------------


@pytest.fixture
def _stub_corpus(tmp_path: Path) -> Path:
    """Minimal MODS XML directory so load_corpus succeeds in main()."""
    xml_dir = tmp_path / "xml"
    xml_dir.mkdir()
    (xml_dir / "1.xml").write_text(
        '<?xml version="1.0"?>\n'
        '<modsCollection xmlns="http://www.loc.gov/mods/v3">\n'
        "  <mods>\n"
        "    <titleInfo><title>Stub title</title></titleInfo>\n"
        "    <abstract>Stub abstract.</abstract>\n"
        "    <identifier type=\"pubmed\">12345</identifier>\n"
        "  </mods>\n"
        "</modsCollection>\n",
        encoding="utf-8",
    )
    return xml_dir


def _write_minimal_baseline(path: Path) -> None:
    """Write a gzip baseline cache `load_baseline_cache` will accept."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "built_at": dt.datetime.now(dt.UTC).isoformat(),
        "params": {},
        "total_docs": 1,
        "total_unigrams": 1,
        "total_bigrams": 0,
        "total_trigrams": 0,
        "total_acronyms": 0,
        "unigrams": {"x": 1},
        "bigrams": {},
        "trigrams": {},
        "acronyms": {},
    }
    with gzip.open(path, "wt", encoding="utf-8") as gz:
        json.dump(payload, gz)


def test_main_no_fulltext_flag_skips_fetch(
    _stub_corpus: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline_path = tmp_path / "baseline.json.gz"
    _write_minimal_baseline(baseline_path)

    calls: list[tuple[tuple, dict]] = []

    def spy(*args, **kwargs):  # noqa: ANN002, ANN003
        calls.append((args, kwargs))
        return {}

    monkeypatch.setattr(
        "scripts.distill_pubmed_keywords.fetch_fulltext_batch", spy
    )

    rc = main(
        [
            "--xml-dir",
            str(_stub_corpus),
            "--baseline-cache",
            str(baseline_path),
            "--no-fulltext",
            "--no-mesh",
            "--json",
            "--output",
            str(tmp_path / "out.json"),
        ]
    )
    assert rc == 0
    assert calls == []


def test_main_default_invokes_fulltext_fetch(
    _stub_corpus: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline_path = tmp_path / "baseline.json.gz"
    _write_minimal_baseline(baseline_path)
    fulltext_cache = tmp_path / "ft"

    received_pmids: list[list[str]] = []

    def spy(pmids, cache_dir, **_kwargs):  # noqa: ANN001, ANN003
        received_pmids.append(list(pmids))
        return {
            "12345": FulltextRecord(
                pmid="12345",
                pmcid="PMC1",
                introduction="intro text",
                methods="methods text",
                results="results text",
                discussion="discussion text",
            )
        }

    monkeypatch.setattr(
        "scripts.distill_pubmed_keywords.fetch_fulltext_batch", spy
    )

    rc = main(
        [
            "--xml-dir",
            str(_stub_corpus),
            "--baseline-cache",
            str(baseline_path),
            "--fulltext-cache",
            str(fulltext_cache),
            "--no-mesh",
            "--json",
            "--output",
            str(tmp_path / "out.json"),
        ]
    )
    assert rc == 0
    assert received_pmids == [["12345"]]
