"""Unit tests for scripts/distill_pubmed_keywords.py.

Network-touching MeSH paths are exercised via the ``fetcher`` injection
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
from rich.console import Console
from scripts.distill_pubmed_keywords import (
    BASELINE_SCHEMA_VERSION,
    BaselineCounts,
    DistillationResult,
    KeywordScore,
    MeshDescriptor,
    MeshQualifier,
    PaperText,
    RankingInputs,
    _foreground_acronyms,
    _foreground_stats_for,
    _llr_score,
    _merged_phrases,
    _ncbi_retry,
    _non_negative_float,
    _parse_args,
    _rank_terms,
    _rank_terms_df,
    _render_query,
    _render_rich_report,
    _resolve_validate_output_paths,
    aggregate_mesh,
    build_baseline_cache,
    build_query_variants,
    distill_keywords,
    fetch_mesh_terms,
    format_hybrid_query,
    format_mesh_query,
    format_structured_query,
    format_titleabstract_query,
    load_baseline_cache,
    load_corpus,
    main,
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
        assert stem_key("viruses") == "virus"

    def test_invariant_biomedical_terms_not_mangled(self) -> None:
        assert stem_key("species") == "species"
        assert stem_key("series") == "series"
        assert stem_key("diabetes") == "diabetes"
        assert stem_key("rabies") == "rabies"

    def test_es_plurals(self) -> None:
        assert stem_key("processes") == "process"
        assert stem_key("classes") == "class"
        assert stem_key("approaches") == "approach"
        assert stem_key("boxes") == "box"

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
        _, _, display = _foreground_stats_for(papers, 1, filter_content=False)
        assert display[("gene",)] == "gene"

    def test_unigram_plural_wins_when_more_common(self) -> None:
        papers = [
            [("gene", "genes"), ("gene", "genes"), ("gene", "gene")],
        ]
        _, _, display = _foreground_stats_for(papers, 1, filter_content=False)
        assert display[("gene",)] == "genes"

    def test_bigram_surface_form(self) -> None:
        papers = [
            [("white", "white"), ("matter", "matter")],
            [("white", "white"), ("matter", "matter")],
        ]
        _, _, display = _foreground_stats_for(papers, 2, filter_content=False)
        assert display[("white", "matter")] == "white matter"

    def test_filtered_surface_forms_cannot_win_modal_display(self) -> None:
        papers = [
            [("result", "results")],
            [("result", "results")],
            [("result", "result")],
        ]

        _, _, unfiltered = _foreground_stats_for(papers, 1, filter_content=False)
        _, _, filtered = _foreground_stats_for(papers, 1, filter_content=True)

        assert unfiltered[("result",)] == "results"
        assert filtered[("result",)] == "result"


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
        inputs = RankingInputs(
            fg_counts=Counter({("common",): 10, ("rare",): 990}),
            fg_doc_freq=Counter({("common",): 1, ("rare",): 5}),
            total_fg=1_000,
            bg_counts=Counter({("common",): 5_000, ("rare",): 2}),
            total_bg=10_000,
        )
        result = _rank_terms(inputs, min_df=1, top_n=10, min_llr=0.0)
        terms = [r.term for r in result]
        assert "rare" in terms
        assert "common" not in terms

    def test_fallback_to_df_when_no_baseline(self) -> None:
        inputs = RankingInputs(
            fg_counts=Counter({("x",): 5, ("y",): 2}),
            fg_doc_freq=Counter({("x",): 3, ("y",): 2}),
            total_fg=7,
        )
        result = _rank_terms(inputs, min_df=1, top_n=10, min_llr=0.0)
        assert result[0].term == "x"
        assert result[0].llr == 0.0  # no LLR computed in DF fallback

    def test_min_df_threshold(self) -> None:
        inputs = RankingInputs(
            fg_counts=Counter({("kept",): 3, ("dropped",): 1}),
            fg_doc_freq=Counter({("kept",): 2, ("dropped",): 1}),
            total_fg=4,
            bg_counts=Counter({("kept",): 0, ("dropped",): 0}),
            total_bg=1_000,
        )
        result = _rank_terms(inputs, min_df=2, top_n=10, min_llr=0.0)
        terms = [r.term for r in result]
        assert "kept" in terms
        assert "dropped" not in terms

    def test_display_lookup_overrides_term_string(self) -> None:
        inputs = RankingInputs(
            fg_counts=Counter({("gene",): 3}),
            fg_doc_freq=Counter({("gene",): 2}),
            total_fg=3,
            bg_counts=Counter({("gene",): 1}),
            total_bg=1_000,
        )
        result = _rank_terms(
            inputs, min_df=1, top_n=10, min_llr=0.0, display={("gene",): "GENES"}
        )
        assert result[0].term == "GENES"

    def test_non_positive_top_n_returns_empty(self) -> None:
        with_bg = RankingInputs(
            fg_counts=Counter({("gene",): 3}),
            fg_doc_freq=Counter({("gene",): 2}),
            total_fg=3,
            bg_counts=Counter({("gene",): 1}),
            total_bg=1_000,
        )
        no_bg = RankingInputs(
            fg_counts=Counter({("gene",): 3}),
            fg_doc_freq=Counter({("gene",): 2}),
            total_fg=3,
        )

        assert _rank_terms(with_bg, min_df=1, top_n=0, min_llr=0.0) == []
        assert _rank_terms(no_bg, min_df=1, top_n=-1, min_llr=0.0) == []

    @pytest.mark.parametrize("bad_min_llr", [float("nan"), float("inf"), -0.1])
    def test_bad_min_llr_raises(self, bad_min_llr: float) -> None:
        inputs = RankingInputs(
            fg_counts=Counter({("gene",): 3}),
            fg_doc_freq=Counter({("gene",): 2}),
            total_fg=3,
            bg_counts=Counter({("gene",): 0}),
            total_bg=1_000,
        )
        with pytest.raises(ValueError, match="min_llr"):
            _rank_terms(inputs, min_df=1, top_n=10, min_llr=bad_min_llr)

    def test_negative_min_df_raises(self) -> None:
        inputs = RankingInputs(
            fg_counts=Counter({("gene",): 3}),
            fg_doc_freq=Counter({("gene",): 2}),
            total_fg=3,
        )
        with pytest.raises(ValueError, match="min_df"):
            _rank_terms(inputs, min_df=-1, top_n=10, min_llr=0.0)

        with pytest.raises(ValueError, match="min_df"):
            _rank_terms_df(inputs, min_df=-1, top_n=10)

    def test_inconsistent_counts_raise_clear_error(self) -> None:
        too_much_foreground = RankingInputs(
            fg_counts=Counter({("gene",): 4}),
            fg_doc_freq=Counter({("gene",): 2}),
            total_fg=3,
            bg_counts=Counter({("gene",): 0}),
            total_bg=1_000,
        )
        with pytest.raises(ValueError, match="exceeds total_fg"):
            _rank_terms(too_much_foreground, min_df=1, top_n=10, min_llr=0.0)

        too_much_baseline = RankingInputs(
            fg_counts=Counter({("gene",): 3}),
            fg_doc_freq=Counter({("gene",): 2}),
            total_fg=3,
            bg_counts=Counter({("gene",): 1_001}),
            total_bg=1_000,
        )
        with pytest.raises(ValueError, match="exceeds total_bg"):
            _rank_terms(too_much_baseline, min_df=1, top_n=10, min_llr=0.0)

    def test_aggregate_count_inconsistencies_raise(self) -> None:
        foreground_sum_too_high = RankingInputs(
            fg_counts=Counter({("gene",): 2, ("marker",): 2}),
            fg_doc_freq=Counter({("gene",): 2, ("marker",): 2}),
            total_fg=3,
            bg_counts=Counter(),
            total_bg=1_000,
        )
        with pytest.raises(ValueError, match="Sum of foreground counts"):
            _rank_terms(foreground_sum_too_high, min_df=1, top_n=10, min_llr=0.0)

        baseline_sum_too_high = RankingInputs(
            fg_counts=Counter({("gene",): 2}),
            fg_doc_freq=Counter({("gene",): 2}),
            total_fg=2,
            bg_counts=Counter({("gene",): 600, ("marker",): 600}),
            total_bg=1_000,
        )
        with pytest.raises(ValueError, match="Sum of baseline counts"):
            _rank_terms(baseline_sum_too_high, min_df=1, top_n=10, min_llr=0.0)

        zero_total_with_baseline_counts = RankingInputs(
            fg_counts=Counter({("gene",): 2}),
            fg_doc_freq=Counter({("gene",): 2}),
            total_fg=2,
            bg_counts=Counter({("gene",): 1}),
            total_bg=0,
        )
        with pytest.raises(ValueError, match="Sum of baseline counts"):
            _rank_terms(
                zero_total_with_baseline_counts, min_df=1, top_n=10, min_llr=0.0
            )

    def test_non_positive_term_counts_are_ignored(self) -> None:
        inputs = RankingInputs(
            fg_counts=Counter({("kept",): 2, ("zero",): 0, ("negative",): -1}),
            fg_doc_freq=Counter({("kept",): 2, ("zero",): 2, ("negative",): 2}),
            total_fg=2,
            bg_counts=Counter(),
            total_bg=1_000,
        )

        result = _rank_terms(inputs, min_df=1, top_n=10, min_llr=0.0)

        assert [r.term for r in result] == ["kept"]


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
            "p2": [
                MeshDescriptor(term="Z", ui="D3", major=False),
                MeshDescriptor(term="W", ui="D4", major=True),
            ],
            "p3": [
                MeshDescriptor(term="Z", ui="D3", major=False),
                MeshDescriptor(term="W", ui="D4", major=True),
            ],
        }
        result = aggregate_mesh(pmid_to_descriptors, top_n=10)
        assert result[0].term == "Z"
        assert result[0].document_frequency == 3
        assert result[1].term == "W"

    def test_duplicate_descriptor_in_single_paper_not_double_counted(
        self,
    ) -> None:
        # Pathological MeSH list: X appears twice in one paper's list
        # (once Major, once Minor). Weight must be 2 (Major wins per
        # paper), not 3 (Major + Minor summed).
        pmid_to_descriptors = {
            "p1": [
                MeshDescriptor(term="X", ui="D1", major=True),
                MeshDescriptor(term="X", ui="D1", major=False),
                MeshDescriptor(term="Y", ui="D2", major=False),
            ],
        }
        result = aggregate_mesh(pmid_to_descriptors, top_n=10)
        by_term = {r.term: r for r in result}
        assert by_term["X"].document_frequency == 1
        assert by_term["X"].total_count == 2
        assert by_term["Y"].document_frequency == 1
        assert by_term["Y"].total_count == 1

    def test_generic_demographic_headings_are_filtered(self) -> None:
        pmid_to_descriptors = {
            "p1": [
                MeshDescriptor(term="Humans", ui="D006801", major=False),
                MeshDescriptor(term="Female", ui="D005260", major=False),
                MeshDescriptor(
                    term="Cerebral Small Vessel Diseases", ui="D000071067", major=True
                ),
            ],
            "p2": [
                MeshDescriptor(term="Humans", ui="D006801", major=False),
                MeshDescriptor(term="Aged", ui="D000368", major=False),
                MeshDescriptor(
                    term="Cerebral Small Vessel Diseases", ui="D000071067", major=False
                ),
            ],
        }

        result = aggregate_mesh(pmid_to_descriptors, top_n=10)

        assert [r.term for r in result] == ["Cerebral Small Vessel Diseases"]
        assert result[0].document_frequency == 2
        assert result[0].total_count == 3

    def test_mesh_terms_are_normalized_before_filtering_and_counting(self) -> None:
        pmid_to_descriptors = {
            "p1": [
                MeshDescriptor(term="  humans  ", ui="D006801", major=False),
                MeshDescriptor(term="White   Matter", ui="D014867", major=False),
            ],
            "p2": [
                MeshDescriptor(term="White Matter", ui="D014867", major=True),
            ],
        }

        result = aggregate_mesh(pmid_to_descriptors, top_n=10)

        assert [r.term for r in result] == ["White Matter"]
        assert result[0].document_frequency == 2
        assert result[0].total_count == 3

    def test_mesh_terms_are_counted_case_insensitively(self) -> None:
        pmid_to_descriptors = {
            "p1": [MeshDescriptor(term="White Matter", ui="D014867", major=False)],
            "p2": [MeshDescriptor(term="white matter", ui="D014867", major=True)],
            "p3": [MeshDescriptor(term="WHITE MATTER", ui="D014867", major=False)],
        }

        result = aggregate_mesh(pmid_to_descriptors, top_n=10)

        assert [r.term for r in result] == ["White Matter"]
        assert result[0].document_frequency == 3
        assert result[0].total_count == 4

    def test_non_positive_top_n_returns_empty(self) -> None:
        pmid_to_descriptors = {
            "p1": [MeshDescriptor(term="X", ui="D1", major=True)],
        }
        assert aggregate_mesh(pmid_to_descriptors, top_n=0) == []
        assert aggregate_mesh(pmid_to_descriptors, top_n=-1) == []


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

    def test_query_terms_are_sanitized_before_quoting(self) -> None:
        scores = [
            KeywordScore(
                term='white "matter"\nhyperintensities',
                document_frequency=3,
                total_count=3,
            ),
            KeywordScore(term='""', document_frequency=2, total_count=2),
            KeywordScore(term="microbleeds", document_frequency=1, total_count=1),
        ]
        q = format_titleabstract_query(scores, top=2)
        assert q == (
            '"white matter hyperintensities"[Title/Abstract] OR '
            '"microbleeds"[Title/Abstract]'
        )

    def test_sanitized_duplicate_query_terms_do_not_consume_top_slots(self) -> None:
        scores = [
            KeywordScore(term='white "matter"', document_frequency=3, total_count=3),
            KeywordScore(term="white matter", document_frequency=2, total_count=2),
            KeywordScore(term="microbleeds", document_frequency=1, total_count=1),
        ]
        q = format_titleabstract_query(scores, top=2)
        assert q == ('"white matter"[Title/Abstract] OR "microbleeds"[Title/Abstract]')

    def test_case_variant_query_terms_do_not_consume_top_slots(self) -> None:
        scores = [
            KeywordScore(term="White Matter", document_frequency=3, total_count=3),
            KeywordScore(term="white matter", document_frequency=2, total_count=2),
            KeywordScore(term="microbleeds", document_frequency=1, total_count=1),
        ]
        q = format_titleabstract_query(scores, top=2)
        assert q == ('"White Matter"[Title/Abstract] OR "microbleeds"[Title/Abstract]')


class TestFormatHybridQuery:
    def test_hybrid_renders_anchor_and_topic_pool(self) -> None:
        scores = [
            KeywordScore(term="CADASIL", document_frequency=5, total_count=5),
            KeywordScore(
                term="white matter hyperintensities",
                document_frequency=4,
                total_count=4,
            ),
        ]
        q = format_hybrid_query("cerebral small vessel disease", scores, phrase_top=10)
        assert q == (
            '"cerebral small vessel disease"[Title/Abstract] AND '
            '("CADASIL"[Title/Abstract] OR '
            '"white matter hyperintensities"[Title/Abstract])'
        )

    def test_hybrid_returns_bare_anchor_when_pool_empty(self) -> None:
        q = format_hybrid_query("cerebral small vessel disease", [], phrase_top=10)
        assert q == '"cerebral small vessel disease"[Title/Abstract]'

    def test_hybrid_drops_anchor_from_pool_when_duplicated(self) -> None:
        scores = [
            KeywordScore(
                term="cerebral small vessel disease",
                document_frequency=10,
                total_count=10,
            ),
            KeywordScore(term="CADASIL", document_frequency=5, total_count=5),
        ]
        q = format_hybrid_query("cerebral small vessel disease", scores, phrase_top=10)
        assert q == (
            '"cerebral small vessel disease"[Title/Abstract] AND '
            '("CADASIL"[Title/Abstract])'
        )

    def test_hybrid_anchor_match_is_case_insensitive(self) -> None:
        scores = [
            KeywordScore(
                term="Cerebral Small Vessel Disease",
                document_frequency=10,
                total_count=10,
            ),
            KeywordScore(term="CADASIL", document_frequency=5, total_count=5),
        ]
        q = format_hybrid_query("cerebral small vessel disease", scores, phrase_top=10)
        # The mixed-case duplicate must still be filtered out.
        assert '"Cerebral Small Vessel Disease"[Title/Abstract]' not in q
        assert '"CADASIL"[Title/Abstract]' in q

    def test_hybrid_drops_pool_terms_that_are_substrings_of_anchor(self) -> None:
        # Pool entries that PubMed positional phrase semantics make
        # tautologically true (substrings of the anchor) must be dropped
        # before rendering — otherwise the OR-clause is decorative and
        # the rendered query misleads the reader.
        scores = [
            KeywordScore(
                term="small vessel disease",
                document_frequency=10,
                total_count=10,
            ),
            KeywordScore(
                term="cerebral small vessel",
                document_frequency=10,
                total_count=10,
            ),
            KeywordScore(term="CADASIL", document_frequency=5, total_count=5),
            KeywordScore(
                term="white matter hyperintensities",
                document_frequency=8,
                total_count=8,
            ),
        ]
        q = format_hybrid_query("cerebral small vessel disease", scores, phrase_top=10)
        assert '"small vessel disease"[Title/Abstract]' not in q
        assert '"cerebral small vessel"[Title/Abstract]' not in q
        # Non-substring pool entries survive — they actually filter.
        assert '"CADASIL"[Title/Abstract]' in q
        assert '"white matter hyperintensities"[Title/Abstract]' in q

    def test_hybrid_substring_dedup_is_case_insensitive(self) -> None:
        scores = [
            KeywordScore(
                term="Small Vessel Disease",
                document_frequency=10,
                total_count=10,
            ),
            KeywordScore(term="CADASIL", document_frequency=5, total_count=5),
        ]
        q = format_hybrid_query("cerebral small vessel disease", scores, phrase_top=10)
        assert '"Small Vessel Disease"[Title/Abstract]' not in q
        assert '"CADASIL"[Title/Abstract]' in q

    def test_hybrid_substring_dedup_uses_sanitized_phrase_text(self) -> None:
        scores = [
            KeywordScore(
                term='small   "vessel"\n disease',
                document_frequency=10,
                total_count=10,
            ),
            KeywordScore(term="CADASIL", document_frequency=5, total_count=5),
        ]

        q = format_hybrid_query(
            "cerebral   small vessel disease", scores, phrase_top=10
        )

        assert '"small vessel disease"[Title/Abstract]' not in q
        assert '"CADASIL"[Title/Abstract]' in q

    def test_hybrid_falls_back_to_bare_anchor_when_pool_fully_subsumed(
        self,
    ) -> None:
        # Every pool term is a substring of the anchor → empty pool after
        # dedupe → render the bare anchor clause.
        scores = [
            KeywordScore(
                term="small vessel disease",
                document_frequency=10,
                total_count=10,
            ),
            KeywordScore(
                term="cerebral small vessel",
                document_frequency=10,
                total_count=10,
            ),
            KeywordScore(
                term="cerebral small",
                document_frequency=8,
                total_count=8,
            ),
        ]
        q = format_hybrid_query("cerebral small vessel disease", scores, phrase_top=10)
        assert q == '"cerebral small vessel disease"[Title/Abstract]'

    def test_hybrid_returns_empty_when_anchor_sanitises_away(self) -> None:
        scores = [KeywordScore(term="CADASIL", document_frequency=5, total_count=5)]
        # Bare quotes and whitespace collapse to empty inside _pubmed_clause.
        assert format_hybrid_query('""', scores, phrase_top=10) == ""


# ---------------------------------------------------------------------------
# Structural-fix flags: --include-unigrams, --include-acronyms,
# --dedupe-substrings (see scripts/_query_diagnose.py for the dedupe helper)
# ---------------------------------------------------------------------------


def _ks(term: str, llr: float, *, df: int = 2, count: int = 2) -> KeywordScore:
    return KeywordScore(term=term, document_frequency=df, total_count=count, llr=llr)


class TestMergedPhrasesExtras:
    def test_default_behavior_unchanged(self) -> None:
        bigrams = [_ks("white matter", 50.0)]
        trigrams = [_ks("small vessel disease", 80.0)]
        pool = _merged_phrases(bigrams, trigrams)
        terms = [k.term for k in pool]
        assert terms == ["small vessel disease", "white matter"]

    def test_includes_unigrams_when_requested(self) -> None:
        bigrams = [_ks("white matter", 50.0)]
        trigrams = [_ks("small vessel disease", 80.0)]
        unigrams = [_ks("cadasil", 150.0), _ks("notch3", 100.0)]
        pool = _merged_phrases(bigrams, trigrams, unigrams=unigrams, include_unigrams=2)
        terms = [k.term for k in pool]
        # Sorted by LLR desc: cadasil(150) > notch3(100) > svd(80) > wm(50)
        assert terms == [
            "cadasil",
            "notch3",
            "small vessel disease",
            "white matter",
        ]

    def test_includes_acronyms_when_requested(self) -> None:
        bigrams = [_ks("white matter", 50.0)]
        trigrams: list[KeywordScore] = []
        acronyms = [_ks("CADASIL", 200.0), _ks("WMH", 90.0)]
        pool = _merged_phrases(bigrams, trigrams, acronyms=acronyms, include_acronyms=2)
        terms = [k.term for k in pool]
        assert "CADASIL" in terms
        assert "WMH" in terms

    def test_include_zero_means_no_extras(self) -> None:
        bigrams = [_ks("white matter", 50.0)]
        trigrams: list[KeywordScore] = []
        unigrams = [_ks("cadasil", 150.0)]
        acronyms = [_ks("CADASIL", 200.0)]
        pool = _merged_phrases(
            bigrams,
            trigrams,
            unigrams=unigrams,
            acronyms=acronyms,
            include_unigrams=0,
            include_acronyms=0,
        )
        assert [k.term for k in pool] == ["white matter"]

    def test_dedupe_substrings_drops_redundant_phrases(self) -> None:
        bigrams = [
            _ks("small vessel", 70.0),
            _ks("vessel disease", 60.0),
        ]
        trigrams = [_ks("small vessel disease", 90.0)]
        pool = _merged_phrases(bigrams, trigrams, dedupe_substrings_flag=True)
        terms = [k.term for k in pool]
        assert terms == ["small vessel disease"]

    def test_dedupe_substrings_uses_sanitized_pubmed_terms(self) -> None:
        bigrams = [
            _ks('small   "vessel"', 70.0),
            _ks("vessel disease", 60.0),
        ]
        trigrams = [_ks("small vessel disease", 90.0)]
        pool = _merged_phrases(bigrams, trigrams, dedupe_substrings_flag=True)
        terms = [k.term for k in pool]
        assert terms == ["small vessel disease"]

    def test_dedupe_substrings_collapses_duplicate_sanitized_terms(self) -> None:
        bigrams = [
            _ks('white   "matter"', 90.0),
            _ks("white matter", 80.0),
            _ks("lacunar infarct", 70.0),
        ]
        pool = _merged_phrases(bigrams, [], dedupe_substrings_flag=True)

        assert [k.term for k in pool] == ['white   "matter"', "lacunar infarct"]

    def test_dedupe_off_keeps_all(self) -> None:
        bigrams = [
            _ks("small vessel", 70.0),
            _ks("vessel disease", 60.0),
        ]
        trigrams = [_ks("small vessel disease", 90.0)]
        pool = _merged_phrases(bigrams, trigrams)
        terms = [k.term for k in pool]
        assert "small vessel" in terms
        assert "vessel disease" in terms

    def test_phrase_top_caps_only_phrases_not_extras(self) -> None:
        bigrams = [_ks("alpha beta", 50.0), _ks("gamma delta", 40.0)]
        trigrams: list[KeywordScore] = []
        unigrams = [_ks("notch3", 100.0)]
        pool = _merged_phrases(
            bigrams,
            trigrams,
            phrase_top=1,
            unigrams=unigrams,
            include_unigrams=1,
        )
        terms = [k.term for k in pool]
        assert "notch3" in terms
        assert "alpha beta" in terms
        assert "gamma delta" not in terms  # capped

    def test_phrase_top_refills_after_substring_dedupe(self) -> None:
        bigrams = [
            _ks("small vessel", 80.0),
            _ks("vessel disease", 70.0),
            _ks("lacunar infarct", 60.0),
        ]
        trigrams = [_ks("small vessel disease", 90.0)]

        pool = _merged_phrases(
            bigrams,
            trigrams,
            phrase_top=2,
            dedupe_substrings_flag=True,
        )

        assert [k.term for k in pool] == ["small vessel disease", "lacunar infarct"]

    def test_phrase_top_zero_drops_phrases_but_keeps_requested_extras(self) -> None:
        bigrams = [_ks("white matter", 50.0)]
        trigrams = [_ks("small vessel disease", 80.0)]
        acronyms = [_ks("CADASIL", 200.0)]

        pool = _merged_phrases(
            bigrams,
            trigrams,
            phrase_top=0,
            acronyms=acronyms,
            include_acronyms=1,
        )

        assert [k.term for k in pool] == ["CADASIL"]


class TestBuildQueryVariantsWithExtras:
    def test_include_acronyms_appears_in_titleabstract(self) -> None:
        mesh: list[KeywordScore] = []
        bigrams = [_ks("white matter", 50.0)]
        trigrams: list[KeywordScore] = []
        unigrams: list[KeywordScore] = []
        acronyms = [_ks("CADASIL", 200.0)]
        variants = build_query_variants(
            mesh_terms=mesh,
            bigrams=bigrams,
            trigrams=trigrams,
            mesh_top=10,
            phrase_top=10,
            unigrams=unigrams,
            acronyms=acronyms,
            include_acronyms=1,
        )
        assert '"CADASIL"[Title/Abstract]' in variants["titleabstract"]

    def test_dedupe_substrings_collapses_in_titleabstract_clause(self) -> None:
        mesh: list[KeywordScore] = []
        bigrams = [
            _ks("small vessel", 70.0),
            _ks("vessel disease", 60.0),
        ]
        trigrams = [_ks("small vessel disease", 90.0)]
        variants = build_query_variants(
            mesh_terms=mesh,
            bigrams=bigrams,
            trigrams=trigrams,
            mesh_top=10,
            phrase_top=10,
            dedupe_substrings_flag=True,
        )
        q = variants["titleabstract"]
        assert '"small vessel disease"[Title/Abstract]' in q
        # The shorter substrings should be gone.
        assert '"small vessel"[Title/Abstract]' not in q
        assert '"vessel disease"[Title/Abstract]' not in q

    def test_default_behavior_matches_no_extras(self) -> None:
        # Without the new flags, build_query_variants should produce
        # output equivalent to its pre-fix behavior (sanity check on
        # backwards compatibility).
        mesh: list[KeywordScore] = []
        bigrams = [_ks("white matter", 50.0)]
        trigrams = [_ks("small vessel disease", 90.0)]
        variants = build_query_variants(
            mesh_terms=mesh,
            bigrams=bigrams,
            trigrams=trigrams,
            mesh_top=10,
            phrase_top=10,
        )
        # Both phrases present in T/A, MeSH empty
        assert '"small vessel disease"[Title/Abstract]' in variants["titleabstract"]
        assert '"white matter"[Title/Abstract]' in variants["titleabstract"]
        assert variants["mesh"] == ""

    def test_hybrid_variant_empty_without_anchor(self) -> None:
        variants = build_query_variants(
            mesh_terms=[],
            bigrams=[_ks("white matter", 50.0)],
            trigrams=[_ks("small vessel disease", 90.0)],
            mesh_top=10,
            phrase_top=10,
        )
        assert "hybrid" in variants
        assert variants["hybrid"] == ""

    def test_hybrid_variant_rendered_with_anchor(self) -> None:
        variants = build_query_variants(
            mesh_terms=[],
            bigrams=[_ks("white matter hyperintensities", 60.0)],
            trigrams=[_ks("small vessel disease", 90.0)],
            mesh_top=10,
            phrase_top=10,
            anchor_phrase="cerebral small vessel disease",
        )
        q = variants["hybrid"]
        assert q.startswith('"cerebral small vessel disease"[Title/Abstract] AND (')
        # "small vessel disease" is a substring of the anchor — drained by
        # PubMed positional phrase semantics — so the substring-dedup in
        # format_hybrid_query drops it from the rendered OR-clause.
        assert '"small vessel disease"[Title/Abstract]' not in q
        # The non-substring pool term survives and is what the AND clause
        # actually filters on.
        assert '"white matter hyperintensities"[Title/Abstract]' in q
        # Hybrid must not leak into other variants.
        ta = variants["titleabstract"]
        assert '"cerebral small vessel disease"[Title/Abstract]' not in ta

    def test_hybrid_anchor_does_not_disturb_other_variants(self) -> None:
        mesh = [KeywordScore(term="Brain", document_frequency=5, total_count=5)]
        bigrams = [_ks("white matter", 50.0)]
        trigrams = [_ks("small vessel disease", 90.0)]
        without = build_query_variants(
            mesh_terms=mesh,
            bigrams=bigrams,
            trigrams=trigrams,
            mesh_top=10,
            phrase_top=10,
        )
        with_anchor = build_query_variants(
            mesh_terms=mesh,
            bigrams=bigrams,
            trigrams=trigrams,
            mesh_top=10,
            phrase_top=10,
            anchor_phrase="cerebral small vessel disease",
        )
        # Only the hybrid slot should differ when an anchor is added.
        for key in ("structured", "mesh", "titleabstract"):
            assert without[key] == with_anchor[key]
        assert without["hybrid"] == ""
        assert with_anchor["hybrid"] != ""

    def test_phrase_top_zero_suppresses_phrase_queries(self) -> None:
        variants = build_query_variants(
            mesh_terms=[],
            bigrams=[_ks("white matter", 50.0)],
            trigrams=[_ks("small vessel disease", 90.0)],
            mesh_top=10,
            phrase_top=0,
        )

        assert variants["titleabstract"] == ""
        assert variants["structured"] == ""

    def test_seed_phrases_inject_into_titleabstract(self) -> None:
        variants = build_query_variants(
            mesh_terms=[],
            bigrams=[_ks("white matter", 50.0)],
            trigrams=[],
            mesh_top=10,
            phrase_top=10,
            seed_phrases=("loci", "intracerebral haemorrhage"),
        )
        ta = variants["titleabstract"]
        assert '"loci"[Title/Abstract]' in ta
        assert '"intracerebral haemorrhage"[Title/Abstract]' in ta
        # Distilled phrases are still present.
        assert '"white matter"[Title/Abstract]' in ta

    def test_seed_phrases_reach_hybrid_secondary_clause(self) -> None:
        variants = build_query_variants(
            mesh_terms=[],
            bigrams=[_ks("white matter hyperintensities", 60.0)],
            trigrams=[],
            mesh_top=10,
            phrase_top=10,
            anchor_phrase="cerebral small vessel disease",
            seed_phrases=("loci", "intracerebral haemorrhage"),
        )
        hybrid = variants["hybrid"]
        expected_prefix = (
            '"cerebral small vessel disease"[Title/Abstract] AND ('
        )
        assert hybrid.startswith(expected_prefix)
        assert '"loci"[Title/Abstract]' in hybrid
        assert '"intracerebral haemorrhage"[Title/Abstract]' in hybrid

    def test_seed_phrases_survive_substring_dedupe(self) -> None:
        # Without seed protection, --dedupe-substrings would drop "loci"
        # as a substring of "risk loci". Seeds are appended after dedupe,
        # so both should appear in the rendered clause.
        variants = build_query_variants(
            mesh_terms=[],
            bigrams=[_ks("risk loci", 70.0)],
            trigrams=[],
            mesh_top=10,
            phrase_top=10,
            dedupe_substrings_flag=True,
            seed_phrases=("loci",),
        )
        ta = variants["titleabstract"]
        assert '"risk loci"[Title/Abstract]' in ta
        assert '"loci"[Title/Abstract]' in ta

    def test_seed_phrases_dedupe_against_existing_pool_entries(self) -> None:
        # When a seed phrase already appears in the distilled pool, the
        # rendered clause must not contain the same clause twice.
        variants = build_query_variants(
            mesh_terms=[],
            bigrams=[_ks("intracerebral haemorrhage", 80.0)],
            trigrams=[],
            mesh_top=10,
            phrase_top=10,
            seed_phrases=("intracerebral haemorrhage",),
        )
        ta = variants["titleabstract"]
        assert ta.count('"intracerebral haemorrhage"[Title/Abstract]') == 1


class TestSeedPhrasesDefaults:
    def test_default_seed_phrases_constant(self) -> None:
        from scripts.distill_pubmed_keywords import DEFAULT_SEED_PHRASES

        assert DEFAULT_SEED_PHRASES == (
            "loci",
            "intracerebral haemorrhage",
            "intracerebral hemorrhage",
        )

    def test_cli_default_seed_phrases_applied(self) -> None:
        args = _parse_args([])
        from scripts.distill_pubmed_keywords import DEFAULT_SEED_PHRASES

        assert tuple(args.seed_phrases) == DEFAULT_SEED_PHRASES

    def test_cli_seed_phrases_explicit_override(self) -> None:
        args = _parse_args(["--seed-phrases", "alpha", "beta gamma"])
        assert args.seed_phrases == ["alpha", "beta gamma"]

    def test_cli_seed_phrases_can_be_emptied(self) -> None:
        # Passing the flag with no values yields an empty list — the
        # documented opt-out path.
        args = _parse_args(["--seed-phrases"])
        assert args.seed_phrases == []


class TestDistillKeywordsWithExtras:
    def test_include_acronyms_reaches_query(self) -> None:
        papers = [
            PaperText(
                pmid="111",
                title="CADASIL is a genetic cause of cerebral small vessel disease",
                abstract="The CADASIL syndrome is caused by NOTCH3 mutations.",
            ),
            PaperText(
                pmid="222",
                title="CADASIL update",
                abstract="A review of CADASIL genetics.",
            ),
        ]
        baseline = BaselineCounts(
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
            min_df=1,
            min_llr=0.0,
            mesh_descriptors=None,
            include_acronyms=3,
        )
        # CADASIL should be a top acronym and now reach the T/A query.
        assert "CADASIL" in [s.term for s in result.acronyms]
        assert '"CADASIL"[Title/Abstract]' in result.query_variants["titleabstract"]

    def test_dedupe_substrings_flag_flows_through(self) -> None:
        papers = [
            PaperText(
                pmid="111",
                title="small vessel disease and small vessel pathology",
                abstract="The vessel disease in small vessel disease patients...",
            ),
            PaperText(
                pmid="222",
                title="small vessel disease overview",
                abstract="More on small vessel disease and vessel disease...",
            ),
        ]
        baseline = BaselineCounts(
            total_docs=1_000,
            unigrams=Counter({("unrelated",): 5_000}),
            bigrams=Counter({("foo", "bar"): 500}),
            trigrams=Counter({("foo", "bar", "baz"): 50}),
            acronyms=Counter(),
            total_unigrams=100_000,
            total_bigrams=10_000,
            total_trigrams=1_000,
            total_acronyms=500,
        )
        result_with = distill_keywords(
            papers,
            baseline=baseline,
            min_df=1,
            min_llr=0.0,
            mesh_descriptors=None,
            dedupe_substrings_flag=True,
        )
        result_without = distill_keywords(
            papers,
            baseline=baseline,
            min_df=1,
            min_llr=0.0,
            mesh_descriptors=None,
            dedupe_substrings_flag=False,
        )
        # Without dedup, both "small vessel" and "small vessel disease"
        # should appear; with dedup, only the longer.
        ta_with = result_with.query_variants["titleabstract"]
        ta_without = result_without.query_variants["titleabstract"]
        assert '"small vessel disease"[Title/Abstract]' in ta_with
        assert '"small vessel"[Title/Abstract]' not in ta_with
        assert '"small vessel"[Title/Abstract]' in ta_without


# ---------------------------------------------------------------------------
# Baseline cache I/O
# ---------------------------------------------------------------------------


def _write_baseline_payload(
    path: Path, *, built_at: str | None = None, override: dict | None = None
) -> None:
    payload = {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "built_at": built_at or dt.datetime.now(dt.UTC).isoformat(),
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
        _write_baseline_payload(path, built_at=dt.datetime.now(dt.UTC).isoformat())
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

    def test_corrupt_cache_raises_runtime_error(self, tmp_path: Path) -> None:
        path = tmp_path / "baseline.json.gz"
        path.write_bytes(b"not gzip")

        with pytest.raises(RuntimeError, match="Could not read baseline cache"):
            load_baseline_cache(path)

    def test_missing_required_cache_field_raises_runtime_error(
        self, tmp_path: Path
    ) -> None:
        # Removing a required field is structurally different from overriding it
        # (override with None still leaves the key present), so this case can't
        # share the parametrized fixture below.
        path = tmp_path / "baseline.json.gz"
        _write_baseline_payload(path, built_at=dt.datetime.now(dt.UTC).isoformat())
        with gzip.open(path, "rt", encoding="utf-8") as gz:
            payload = json.load(gz)
        del payload["trigrams"]
        with gzip.open(path, "wt", encoding="utf-8") as gz:
            json.dump(payload, gz)

        with pytest.raises(RuntimeError, match="missing required"):
            load_baseline_cache(path)

    @pytest.mark.parametrize(
        ("override", "match"),
        [
            pytest.param(
                {"unigrams": ["not", "a", "mapping"]},
                "unigrams",
                id="non-mapping-counter",
            ),
            pytest.param(
                {"acronyms": {"MRI": -1}},
                "negative",
                id="negative-count",
            ),
            pytest.param(
                {"unigrams": {"gene": 1.8}},
                "non-integer",
                id="float-count",
            ),
            pytest.param(
                {"total_docs": True},
                "non-integer",
                id="bool-total",
            ),
            pytest.param(
                {"built_at": "not-a-timestamp"},
                "built_at",
                id="invalid-built-at",
            ),
            pytest.param(
                {"built_at": ""},
                "built_at",
                id="blank-built-at",
            ),
            pytest.param(
                {"bigrams": {"one-token": 1}},
                "bigrams",
                id="wrong-ngram-arity",
            ),
            pytest.param(
                {"unigrams": {"white matter": 1}},
                "unigrams",
                id="whitespace-unigram-key",
            ),
            pytest.param(
                {"acronyms": {"BAD KEY": 1}},
                "acronyms",
                id="whitespace-acronym-key",
            ),
            pytest.param(
                {"total_unigrams": 1},
                "total_unigrams",
                id="total-smaller-than-counts",
            ),
            pytest.param(
                {
                    "total_docs": 0,
                    "total_unigrams": 0,
                    "total_bigrams": 0,
                    "total_acronyms": 0,
                    "unigrams": {},
                    "bigrams": {},
                    "acronyms": {},
                },
                "total_docs",
                id="zero-document-baseline",
            ),
        ],
    )
    def test_malformed_baseline_payload_raises_runtime_error(
        self, tmp_path: Path, override: dict, match: str
    ) -> None:
        path = tmp_path / "baseline.json.gz"
        _write_baseline_payload(
            path,
            built_at=dt.datetime.now(dt.UTC).isoformat(),
            override=override,
        )

        with pytest.raises(RuntimeError, match=match):
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

    def test_future_built_at_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "baseline.json.gz"
        future = (dt.datetime.now(dt.UTC) + dt.timedelta(days=2)).isoformat()
        _write_baseline_payload(path, built_at=future)

        with pytest.raises(RuntimeError, match="future"):
            load_baseline_cache(path)

    def test_naive_built_at_is_tolerated(self, tmp_path: Path) -> None:
        # A baseline cache produced by a tool that emitted a timezone-naive
        # built_at must still load: the age subtraction below would raise
        # TypeError on a naive/aware mismatch unless the loader normalizes.
        path = tmp_path / "baseline.json.gz"
        _write_baseline_payload(path, built_at="2025-01-01T00:00:00")
        bc = load_baseline_cache(path)
        assert bc.total_docs == 10

    def test_build_rejects_invalid_sizes_before_network(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="size"):
            build_baseline_cache(
                0,
                tmp_path / "baseline.json.gz",
                pdat_range="2020:2024",
            )
        with pytest.raises(ValueError, match="batch_size"):
            build_baseline_cache(
                10,
                tmp_path / "baseline.json.gz",
                pdat_range="2020:2024",
                batch_size=0,
            )

    def test_build_rejects_reversed_pdat_before_network(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="start year"):
            build_baseline_cache(
                10,
                tmp_path / "baseline.json.gz",
                pdat_range="2024:2020",
            )

    @pytest.mark.parametrize(
        ("esearch_response", "match"),
        [
            ([], "malformed baseline response"),
            ({"IdList": "15905468"}, "malformed IdList"),
        ],
    )
    def test_build_rejects_malformed_esearch_response(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        esearch_response: object,
        match: str,
    ) -> None:
        class _FakeEntrez:
            @staticmethod
            def esearch(**_kwargs: object) -> None:
                return None

            @staticmethod
            def efetch(**_kwargs: object) -> None:
                return None

            @staticmethod
            def read(_handle: object) -> object:
                return {}

        def fake_configure(
            *, email: str | None = None, api_key: str | None = None
        ) -> None:
            return None

        def fake_retry(
            _fn: object,
            *args: object,
            _reader: object,
            **kwargs: object,
        ) -> object:
            return esearch_response

        monkeypatch.setitem(
            __import__("sys").modules,
            "Bio",
            type("M", (), {"Entrez": _FakeEntrez}),
        )
        monkeypatch.setattr(
            "scripts.distill_pubmed_keywords._configure_entrez", fake_configure
        )
        monkeypatch.setattr("scripts.distill_pubmed_keywords._ncbi_retry", fake_retry)
        monkeypatch.setattr(
            "scripts.distill_pubmed_keywords._ncbi_sleep", lambda _k: None
        )

        with pytest.raises(RuntimeError, match=match):
            build_baseline_cache(
                10,
                tmp_path / "baseline.json.gz",
                pdat_range="2020:2024",
            )


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

    def test_pmid_absent_from_response_is_not_cached(self, tmp_path: Path) -> None:
        # If NCBI's efetch response omits a PMID (truncation, partial
        # response), the function must not write a permanent empty
        # descriptors cache entry for it — that would silently drop the
        # paper's MeSH contribution on every future run with no recovery.
        # A missing PMID should be retried rather than cached as empty.
        fixture = _FIXTURES.joinpath("mesh_sample.xml").read_bytes()

        def stub_fetcher(batch: list[str]) -> bytes:
            return fixture

        # PMID "12345678" is not in the fixture's PubmedArticleSet.
        result = fetch_mesh_terms(
            ["15905468", "12345678"],
            cache_dir=tmp_path,
            fetcher=stub_fetcher,
        )

        # PMID present in the response is cached and returned.
        assert "15905468" in result
        assert (tmp_path / "15905468.json").exists()

        # PMID absent from the response is NOT cached and NOT in output —
        # the caller can retry it next run.
        assert "12345678" not in result
        assert not (tmp_path / "12345678.json").exists()

    def test_refuses_non_numeric_pmid_at_cache_write(self, tmp_path: Path) -> None:
        # Defense in depth: even if a non-numeric PMID slips past
        # parse_mods_file (e.g., a direct caller bypasses the corpus
        # loader), the cache write must refuse to use it as a filename
        # so a path-traversal value can't escape cache_dir.
        fixture = _FIXTURES.joinpath("mesh_sample.xml").read_bytes()

        def stub_fetcher(batch: list[str]) -> bytes:
            return fixture

        result = fetch_mesh_terms(
            ["../escape"],
            cache_dir=tmp_path,
            fetcher=stub_fetcher,
        )

        assert "../escape" not in result
        # The traversal would have landed in tmp_path.parent if unguarded.
        assert not (tmp_path.parent / "escape.json").exists()

    def test_duplicate_pmids_are_deduped_before_fetch(self, tmp_path: Path) -> None:
        fixture = _FIXTURES.joinpath("mesh_sample.xml").read_bytes()
        calls: list[list[str]] = []
        progress: list[tuple[int, int]] = []

        def stub_fetcher(batch: list[str]) -> bytes:
            calls.append(list(batch))
            return fixture

        result = fetch_mesh_terms(
            ["15905468", "15905468", "23649698"],
            cache_dir=tmp_path,
            fetcher=stub_fetcher,
            progress_callback=lambda c, t: progress.append((c, t)),
        )

        assert calls == [["15905468", "23649698"]]
        assert set(result) == {"15905468", "23649698"}
        assert progress[-1] == (2, 2)

    def test_cache_pmid_mismatch_is_refetched(self, tmp_path: Path) -> None:
        (tmp_path / "15905468.json").write_text(
            json.dumps(
                {
                    "pmid": "23649698",
                    "descriptors": [{"term": "Wrong", "ui": "D0", "major": False}],
                    "fetched_at": "2025-01-01T00:00:00",
                }
            ),
            encoding="utf-8",
        )
        fixture = _FIXTURES.joinpath("mesh_sample.xml").read_bytes()
        calls: list[list[str]] = []

        def stub_fetcher(batch: list[str]) -> bytes:
            calls.append(list(batch))
            return fixture

        result = fetch_mesh_terms(
            ["15905468"], cache_dir=tmp_path, fetcher=stub_fetcher
        )

        assert calls == [["15905468"]]
        assert result["15905468"][0].term != "Wrong"

    def test_cache_string_major_flag_is_refetched(self, tmp_path: Path) -> None:
        # bool("False") is True in Python; cached flags must be real JSON
        # booleans or a hand-edited/corrupt cache can silently promote
        # minor headings to major topics.
        (tmp_path / "15905468.json").write_text(
            json.dumps(
                {
                    "pmid": "15905468",
                    "descriptors": [
                        {
                            "term": "Wrong",
                            "ui": "D0",
                            "major": "False",
                            "qualifiers": [],
                        }
                    ],
                    "fetched_at": "2025-01-01T00:00:00",
                }
            ),
            encoding="utf-8",
        )
        fixture = _FIXTURES.joinpath("mesh_sample.xml").read_bytes()
        calls: list[list[str]] = []

        def stub_fetcher(batch: list[str]) -> bytes:
            calls.append(list(batch))
            return fixture

        result = fetch_mesh_terms(
            ["15905468"], cache_dir=tmp_path, fetcher=stub_fetcher
        )

        assert calls == [["15905468"]]
        assert result["15905468"][0].term != "Wrong"

    @pytest.mark.parametrize(
        "payload",
        [
            {"pmid": "15905468", "fetched_at": "2025-01-01T00:00:00"},
            {
                "descriptors": [
                    {"term": "Wrong", "ui": "D0", "major": False, "qualifiers": []}
                ],
                "fetched_at": "2025-01-01T00:00:00",
            },
            {
                "pmid": "15905468",
                "descriptors": [
                    {"term": "Wrong", "ui": ["D0"], "major": False, "qualifiers": []}
                ],
                "fetched_at": "2025-01-01T00:00:00",
            },
        ],
    )
    def test_malformed_cache_schema_is_refetched(
        self, tmp_path: Path, payload: dict
    ) -> None:
        (tmp_path / "15905468.json").write_text(
            json.dumps(payload),
            encoding="utf-8",
        )
        fixture = _FIXTURES.joinpath("mesh_sample.xml").read_bytes()
        calls: list[list[str]] = []

        def stub_fetcher(batch: list[str]) -> bytes:
            calls.append(list(batch))
            return fixture

        result = fetch_mesh_terms(
            ["15905468"], cache_dir=tmp_path, fetcher=stub_fetcher
        )

        assert calls == [["15905468"]]
        assert result["15905468"][0].term != "Wrong"

    def test_rejects_non_positive_batch_size(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="batch_size"):
            fetch_mesh_terms(["15905468"], cache_dir=tmp_path, batch_size=0)

    def test_missing_entrez_credentials_keeps_cached_mesh(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "111.json").write_text(
            json.dumps(
                {
                    "pmid": "111",
                    "descriptors": [
                        {
                            "term": "Brain",
                            "ui": "D001921",
                            "major": True,
                            "qualifiers": [],
                        }
                    ],
                    "fetched_at": "2025-01-01T00:00:00",
                }
            ),
            encoding="utf-8",
        )

        def missing_credentials(*, email=None, api_key=None):  # noqa: ANN001
            raise RuntimeError("ENTREZ_EMAIL is required")

        monkeypatch.setattr(
            "scripts.distill_pubmed_keywords._configure_entrez",
            missing_credentials,
        )
        progress: list[tuple[int, int]] = []

        result = fetch_mesh_terms(
            ["111", "222"],
            cache_dir=tmp_path,
            progress_callback=lambda c, t: progress.append((c, t)),
        )

        assert list(result) == ["111"]
        assert result["111"][0].term == "Brain"
        assert progress[-1] == (2, 2)


def _write_mods_xml(path: Path, inner: str) -> None:
    """Write a MODS XML record with the given ``<mods>`` block as inner content.

    Wraps ``inner`` in the standard ``<?xml?>`` declaration and
    ``<modsCollection xmlns=...>`` envelope so tests can focus on the
    structure they're exercising.
    """
    path.write_text(
        f'<?xml version="1.0"?>\n'
        f'<modsCollection xmlns="http://www.loc.gov/mods/v3">\n'
        f"{inner}\n"
        f"</modsCollection>\n",
        encoding="utf-8",
    )


def _write_mods(
    path: Path, *, pmid: str | None, title: str = "T", abstract: str = "A."
) -> None:
    """Write a minimal MODS XML record.

    ``pmid=None`` omits the ``<identifier type="pubmed">`` element
    entirely, modelling MODS records that lack a PubMed identifier
    (e.g. files converted from BibTeX where the PMID is not in the
    source bibliography).
    """
    identifier_line = (
        f'    <identifier type="pubmed">{pmid}</identifier>\n'
        if pmid is not None
        else ""
    )
    _write_mods_xml(
        path,
        "  <mods>\n"
        f"    <titleInfo><title>{title}</title></titleInfo>\n"
        f"    <abstract>{abstract}</abstract>\n"
        f"{identifier_line}"
        "  </mods>",
    )


class TestParseMods:
    def test_non_numeric_pmid_is_dropped(self, tmp_path: Path) -> None:
        # A tampered MODS record with a path-traversal PMID must not
        # propagate the value — downstream code interpolates it into
        # a cache filename.
        from scripts.distill_pubmed_keywords import parse_mods_file

        path = tmp_path / "bad.xml"
        _write_mods(path, pmid="../etc/passwd")
        paper = parse_mods_file(path)
        assert paper is not None
        assert paper.pmid is None
        assert paper.title == "T"
        assert paper.abstract == "A."

    def test_numeric_pmid_is_preserved(self, tmp_path: Path) -> None:
        from scripts.distill_pubmed_keywords import parse_mods_file

        path = tmp_path / "ok.xml"
        _write_mods(path, pmid="15905468")
        paper = parse_mods_file(path)
        assert paper is not None
        assert paper.pmid == "15905468"

    def test_root_mods_is_not_replaced_by_nested_related_mods(
        self, tmp_path: Path
    ) -> None:
        from scripts.distill_pubmed_keywords import parse_mods_file

        path = tmp_path / "root-mods.xml"
        path.write_text(
            '<?xml version="1.0"?>\n'
            '<mods xmlns="http://www.loc.gov/mods/v3">\n'
            "  <titleInfo><title>Article title</title></titleInfo>\n"
            "  <abstract>Article abstract.</abstract>\n"
            '  <identifier type="pubmed">15905468</identifier>\n'
            '  <relatedItem type="host">\n'
            "    <mods><titleInfo><title>Journal title</title></titleInfo></mods>\n"
            "  </relatedItem>\n"
            "</mods>\n",
            encoding="utf-8",
        )

        paper = parse_mods_file(path)

        assert paper is not None
        assert paper.title == "Article title"
        assert paper.abstract == "Article abstract."
        assert paper.pmid == "15905468"

    def test_falls_back_to_filename_pmid_when_mods_has_none(
        self, tmp_path: Path
    ) -> None:
        # MODS records converted from BibTeX often lack an inner
        # PubMed identifier. When the filename stem is a valid PMID
        # (as the user has named them), surface it so MeSH enrichment
        # can run for these papers too.
        from scripts.distill_pubmed_keywords import parse_mods_file

        path = tmp_path / "23867200.xml"
        _write_mods(path, pmid=None)
        paper = parse_mods_file(path)
        assert paper is not None
        assert paper.pmid == "23867200"

    def test_filename_pmid_fallback_rejects_non_numeric_stem(
        self, tmp_path: Path
    ) -> None:
        # The fallback must still validate the stem via _is_valid_pmid;
        # otherwise non-numeric filenames would be interpolated into
        # cache paths.
        from scripts.distill_pubmed_keywords import parse_mods_file

        path = tmp_path / "wardlaw2013.xml"
        _write_mods(path, pmid=None)
        paper = parse_mods_file(path)
        assert paper is not None
        assert paper.pmid is None

    def test_mods_pmid_takes_precedence_over_filename(self, tmp_path: Path) -> None:
        # The inner MODS identifier is the explicit/authoritative source;
        # the filename only fills in when the MODS lacks one.
        from scripts.distill_pubmed_keywords import parse_mods_file

        path = tmp_path / "99999.xml"
        _write_mods(path, pmid="15905468")
        paper = parse_mods_file(path)
        assert paper is not None
        assert paper.pmid == "15905468"

    def test_multiple_abstract_elements_are_joined(self, tmp_path: Path) -> None:
        from scripts.distill_pubmed_keywords import parse_mods_file

        path = tmp_path / "multi-abstract.xml"
        path.write_text(
            '<?xml version="1.0"?>\n'
            '<modsCollection xmlns="http://www.loc.gov/mods/v3">\n'
            "  <mods>\n"
            "    <titleInfo><title>T</title></titleInfo>\n"
            "    <abstract>First part.</abstract>\n"
            "    <abstract>Second part.</abstract>\n"
            '    <identifier type="pubmed">15905468</identifier>\n'
            "  </mods>\n"
            "</modsCollection>\n",
            encoding="utf-8",
        )

        paper = parse_mods_file(path)

        assert paper is not None
        assert paper.abstract == "First part. Second part."

    def test_primary_title_info_preferred_over_alternate(self, tmp_path: Path) -> None:
        from scripts.distill_pubmed_keywords import parse_mods_file

        path = tmp_path / "alternate-title.xml"
        _write_mods_xml(
            path,
            "  <mods>\n"
            '    <titleInfo type="abbreviated"><title>Abbrev</title></titleInfo>\n'
            "    <titleInfo><title>Full article title</title></titleInfo>\n"
            "    <abstract>A.</abstract>\n"
            '    <identifier type="pubmed">15905468</identifier>\n'
            "  </mods>",
        )

        paper = parse_mods_file(path)

        assert paper is not None
        assert paper.title == "Full article title"

    def test_empty_primary_title_falls_back_to_populated_alternate(
        self, tmp_path: Path
    ) -> None:
        from scripts.distill_pubmed_keywords import parse_mods_file

        path = tmp_path / "empty-primary-title.xml"
        _write_mods_xml(
            path,
            "  <mods>\n"
            "    <titleInfo><title>   </title></titleInfo>\n"
            '    <titleInfo type="alternative">'
            "<title>Recovered title</title></titleInfo>\n"
            "    <abstract>A.</abstract>\n"
            '    <identifier type="pubmed">15905468</identifier>\n'
            "  </mods>",
        )

        paper = parse_mods_file(path)

        assert paper is not None
        assert paper.title == "Recovered title"

    def test_pmid_identifier_type_is_case_insensitive_and_accepts_pmid(
        self, tmp_path: Path
    ) -> None:
        from scripts.distill_pubmed_keywords import parse_mods_file

        path = tmp_path / "pmid-type.xml"
        _write_mods_xml(
            path,
            "  <mods>\n"
            "    <titleInfo><title>T</title></titleInfo>\n"
            "    <abstract>A.</abstract>\n"
            '    <identifier type="PMID">15905468</identifier>\n'
            "  </mods>",
        )

        paper = parse_mods_file(path)

        assert paper is not None
        assert paper.pmid == "15905468"

    def test_pmid_identifier_accepts_pubmed_id_label(self, tmp_path: Path) -> None:
        from scripts.distill_pubmed_keywords import parse_mods_file

        path = tmp_path / "pubmed-id-type.xml"
        _write_mods_xml(
            path,
            "  <mods>\n"
            "    <titleInfo><title>T</title></titleInfo>\n"
            "    <abstract>A.</abstract>\n"
            '    <identifier type="PubMed ID">15905468</identifier>\n'
            "  </mods>",
        )

        paper = parse_mods_file(path)

        assert paper is not None
        assert paper.pmid == "15905468"

    def test_later_valid_pmid_wins_after_malformed_identifier(
        self, tmp_path: Path
    ) -> None:
        from scripts.distill_pubmed_keywords import parse_mods_file

        path = tmp_path / "multiple-pmids.xml"
        _write_mods_xml(
            path,
            "  <mods>\n"
            "    <titleInfo><title>T</title></titleInfo>\n"
            "    <abstract>A.</abstract>\n"
            '    <identifier type="pubmed">../escape</identifier>\n'
            '    <identifier type="pmid">15905468</identifier>\n'
            "  </mods>",
        )

        paper = parse_mods_file(path)

        assert paper is not None
        assert paper.pmid == "15905468"

    def test_xml_text_whitespace_is_collapsed(self, tmp_path: Path) -> None:
        from scripts.distill_pubmed_keywords import parse_mods_file

        path = tmp_path / "whitespace.xml"
        _write_mods_xml(
            path,
            "  <mods>\n"
            "    <titleInfo><title>White\n      matter</title></titleInfo>\n"
            "    <abstract>First\n\n      second.</abstract>\n"
            '    <identifier type="pubmed">15905468</identifier>\n'
            "  </mods>",
        )

        paper = parse_mods_file(path)

        assert paper is not None
        assert paper.title == "White matter"
        assert paper.abstract == "First second."

    def test_mods_collection_skips_leading_empty_direct_mods(
        self, tmp_path: Path
    ) -> None:
        from scripts.distill_pubmed_keywords import parse_mods_file

        path = tmp_path / "leading-empty-mods.xml"
        _write_mods_xml(
            path,
            "  <mods />\n"
            "  <mods>\n"
            "    <titleInfo><title>Recovered article</title></titleInfo>\n"
            "    <abstract>A.</abstract>\n"
            '    <identifier type="pubmed">15905468</identifier>\n'
            "  </mods>",
        )

        paper = parse_mods_file(path)

        assert paper is not None
        assert paper.title == "Recovered article"
        assert paper.pmid == "15905468"

    def test_mods_collection_falls_through_empty_direct_mods_to_nested_article(
        self, tmp_path: Path
    ) -> None:
        from scripts.distill_pubmed_keywords import parse_mods_file

        path = tmp_path / "nested-after-empty-direct-mods.xml"
        path.write_text(
            '<?xml version="1.0"?>\n'
            '<modsCollection xmlns="http://www.loc.gov/mods/v3">\n'
            "  <mods />\n"
            "  <record>\n"
            "    <mods>\n"
            "      <titleInfo><title>Nested article</title></titleInfo>\n"
            "      <abstract>A.</abstract>\n"
            '      <identifier type="pubmed">15905468</identifier>\n'
            "    </mods>\n"
            "  </record>\n"
            "</modsCollection>\n",
            encoding="utf-8",
        )

        paper = parse_mods_file(path)

        assert paper is not None
        assert paper.title == "Nested article"
        assert paper.pmid == "15905468"

    def test_mods_collection_prefers_direct_article_signal_over_title_only_record(
        self, tmp_path: Path
    ) -> None:
        from scripts.distill_pubmed_keywords import parse_mods_file

        path = tmp_path / "direct-title-only-before-article.xml"
        _write_mods_xml(
            path,
            "  <mods>\n"
            "    <titleInfo><title>Journal title only</title></titleInfo>\n"
            "  </mods>\n"
            "  <mods>\n"
            "    <titleInfo><title>Article title</title></titleInfo>\n"
            "    <abstract>Article abstract.</abstract>\n"
            '    <identifier type="pubmed">15905468</identifier>\n'
            "  </mods>",
        )

        paper = parse_mods_file(path)

        assert paper is not None
        assert paper.title == "Article title"
        assert paper.abstract == "Article abstract."
        assert paper.pmid == "15905468"

    def test_mods_collection_prefers_nested_article_signal_over_host_title(
        self, tmp_path: Path
    ) -> None:
        from scripts.distill_pubmed_keywords import parse_mods_file

        path = tmp_path / "nested-host-before-article.xml"
        path.write_text(
            '<?xml version="1.0"?>\n'
            '<modsCollection xmlns="http://www.loc.gov/mods/v3">\n'
            "  <wrapper>\n"
            "    <mods><titleInfo><title>Host journal</title></titleInfo></mods>\n"
            "    <mods>\n"
            "      <titleInfo><title>Nested article</title></titleInfo>\n"
            "      <abstract>A.</abstract>\n"
            '      <identifier type="pubmed">15905468</identifier>\n'
            "    </mods>\n"
            "  </wrapper>\n"
            "</modsCollection>\n",
            encoding="utf-8",
        )

        paper = parse_mods_file(path)

        assert paper is not None
        assert paper.title == "Nested article"
        assert paper.pmid == "15905468"


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
        tf, df, _ = _foreground_stats_for(pairs, n=1, filter_content=True)
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
        tf, _df, _ = _foreground_stats_for(pairs, n=2, filter_content=True)
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
        tf, _df, _ = _foreground_stats_for(pairs, n=1, filter_content=True)
        assert ("result",) not in tf
        assert ("method",) not in tf
        assert ("intervention",) not in tf
        assert ("measurement",) not in tf
        assert ("gene",) in tf

    def test_acronym_detection(self) -> None:
        paper_tokens = [
            ["WMH", "and", "CADASIL", "MRI"],
            ["WMH", "study", "MRI", "and", "SVD"],
        ]
        tf, df = _foreground_acronyms(paper_tokens)
        assert tf["WMH"] == 2
        assert df["WMH"] == 2
        assert tf["MRI"] == 2
        assert tf["CADASIL"] == 1

    def test_structured_abstract_labels_are_not_acronyms(self) -> None:
        paper_tokens = [
            ["FINDINGS", "and", "FUNDING", "WMH", "was", "measured", "by", "MRI"]
        ]

        tf, df = _foreground_acronyms(paper_tokens)

        assert "FINDINGS" not in tf
        assert "FUNDING" not in tf
        assert "FINDINGS" not in df
        assert "FUNDING" not in df
        assert tf["WMH"] == 1
        assert tf["MRI"] == 1

    def test_document_artifact_labels_are_filtered_from_phrases(self) -> None:
        pairs = [
            [
                ("supplementary", "supplementary"),
                ("table", "table"),
                ("white", "white"),
                ("matter", "matter"),
            ]
        ]

        tf, _df, _ = _foreground_stats_for(pairs, n=2, filter_content=True)

        assert ("supplementary", "table") not in tf
        assert ("table", "white") not in tf
        assert ("white", "matter") in tf

    def test_url_boilerplate_is_filtered_from_phrases(self) -> None:
        pairs = [
            [
                ("doi", "doi"),
                ("org", "org"),
                ("dryad", "dryad"),
                ("lacunar", "lacunar"),
                ("stroke", "stroke"),
            ]
        ]

        tf, _df, _ = _foreground_stats_for(pairs, n=3, filter_content=True)

        assert ("doi", "org", "dryad") not in tf
        assert ("org", "dryad", "lacunar") not in tf
        assert ("dryad", "lacunar", "stroke") not in tf


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


class TestNcbiRetry:
    def test_reader_retried_when_mid_read_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A transient mid-read error must be retried inside the loop,
        # not propagated out. Before this fix, only the handle-open call
        # was wrapped; reads (the actual data transfer) ran unprotected.
        import scripts.distill_pubmed_keywords as mod

        # Skip the configured backoff sleeps so the test is fast.
        monkeypatch.setattr(mod.time, "sleep", lambda _s: None)

        attempts = {"open": 0, "read": 0}

        class _Handle:
            def close(self) -> None:
                pass

        def fake_fn() -> _Handle:
            attempts["open"] += 1
            return _Handle()

        def fake_reader(_h: _Handle) -> str:
            attempts["read"] += 1
            if attempts["read"] < 3:
                raise OSError("simulated mid-read failure")
            return "payload"

        result = _ncbi_retry(fake_fn, _reader=fake_reader)
        assert result == "payload"
        assert attempts["open"] == 3
        assert attempts["read"] == 3

    def test_reader_exhausts_and_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import scripts.distill_pubmed_keywords as mod

        monkeypatch.setattr(mod.time, "sleep", lambda _s: None)

        class _Handle:
            def close(self) -> None:
                pass

        def fake_fn() -> _Handle:
            return _Handle()

        def always_fails(_h: _Handle) -> str:
            raise OSError("persistent failure")

        with pytest.raises(OSError, match="persistent failure"):
            _ncbi_retry(fake_fn, _reader=always_fails)


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_non_negative_float_rejects_non_finite(value: str) -> None:
    with pytest.raises(Exception, match="finite"):
        _non_negative_float(value)


# ---------------------------------------------------------------------------
# PaperText.combined
# ---------------------------------------------------------------------------


def test_paper_text_combined_uses_title_and_abstract_only() -> None:
    paper = PaperText(pmid="1", title="Title", abstract="Abstract.")
    assert paper.combined == "Title Abstract."


# ---------------------------------------------------------------------------
# main() — CLI wiring
# ---------------------------------------------------------------------------


@pytest.fixture
def _stub_corpus(tmp_path: Path) -> Path:
    """Minimal MODS XML directory so load_corpus succeeds in main()."""
    xml_dir = tmp_path / "xml"
    xml_dir.mkdir()
    _write_mods(
        xml_dir / "1.xml", pmid="12345", title="Stub title", abstract="Stub abstract."
    )
    return xml_dir


def test_main_accepts_title_abstract_only_run(
    _stub_corpus: Path,
    tmp_path: Path,
) -> None:
    baseline_path = tmp_path / "baseline.json.gz"
    _write_baseline_payload(baseline_path)

    rc = main(
        [
            "--xml-dir",
            str(_stub_corpus),
            "--baseline-cache",
            str(baseline_path),
            "--no-mesh",
            "--json",
            "--output",
            str(tmp_path / "out.json"),
        ]
    )
    assert rc == 0


def test_main_hybrid_without_explicit_anchor_uses_default() -> None:
    # The --anchor-phrase default was changed from None to the
    # empirically validated DEFAULT_ANCHOR_PHRASE so the no-flag
    # invocation produces a non-empty hybrid variant. Verify the parser
    # supplies the default rather than erroring (the prior behavior).
    from scripts.distill_pubmed_keywords import DEFAULT_ANCHOR_PHRASE

    args = _parse_args(["--query-format", "hybrid"])
    assert args.anchor_phrase == DEFAULT_ANCHOR_PHRASE


def test_parse_args_build_baseline_ignores_query_specific_validation() -> None:
    args = _parse_args(["--build-baseline", "--query-format", "hybrid"])

    assert args.build_baseline is True
    assert args.query_format == "hybrid"


def test_main_anchor_phrase_whitespace_only_errors(_stub_corpus: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "--xml-dir",
                str(_stub_corpus),
                "--query-format",
                "hybrid",
                "--anchor-phrase",
                "   ",
            ]
        )
    assert excinfo.value.code == 2


def test_main_diagnose_and_validate_together_errors(_stub_corpus: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "--xml-dir",
                str(_stub_corpus),
                "--diagnose",
                "--validate",
            ]
        )
    assert excinfo.value.code == 2


def test_main_hybrid_with_anchor_phrase_succeeds(
    _stub_corpus: Path,
    tmp_path: Path,
) -> None:
    baseline_path = tmp_path / "baseline.json.gz"
    _write_baseline_payload(baseline_path)
    out_path = tmp_path / "out.json"

    rc = main(
        [
            "--xml-dir",
            str(_stub_corpus),
            "--baseline-cache",
            str(baseline_path),
            "--no-mesh",
            "--query-format",
            "hybrid",
            "--anchor-phrase",
            "cerebral small vessel disease",
            "--json",
            "--output",
            str(out_path),
        ]
    )
    assert rc == 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    hybrid = payload["query_variants"]["hybrid"]
    assert hybrid.startswith('"cerebral small vessel disease"[Title/Abstract]')


def test_main_validate_writes_markdown_and_json(
    _stub_corpus: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--validate` should run the validator, write Markdown + JSON, return 0.

    The whole network + LLM pipeline is mocked at ``run_validate`` so the
    CLI dispatch is exercised end-to-end without hitting NCBI or
    Anthropic. Verifies the CLI:

    - imports SVD_QUERY and the validator without erroring
    - picks a non-empty distilled query variant
    - resolves `--validate-output` and writes the .md + matching .json
    - returns rc == 0 on success
    """
    baseline_path = tmp_path / "baseline.json.gz"
    _write_baseline_payload(baseline_path)
    validate_dir = tmp_path / "validate-out"

    from scripts._query_validate import (
        QueryValidation,
        RecallFloor,
        RelevanceScore,
        ValidationReport,
    )

    captured: dict = {}

    def fake_run_validate(**kwargs):  # noqa: ANN001, ANN202
        captured.update(kwargs)
        scores = [RelevanceScore("1", True, "mesh", 1.0, "test", "Stroke")]
        qv = QueryValidation(
            label="x",
            query="q",
            total_pmids=1,
            truncated=False,
            sample_pmids=["1"],
            scores=scores,
            recall_floor=RecallFloor(retrieved=1, total_gold=1, missing=[]),
        )
        return ValidationReport(
            query=qv,
            relevant_mesh_set=["Stroke"],
            gold_pmids=["1"],
            sample_size=10,
            seed=0,
            validate_since="2024/01/01",
            validate_until=None,
            llm_model="claude-haiku-4-5-20251001",
        )

    monkeypatch.setattr("scripts._query_validate.run_validate", fake_run_validate)

    rc = main(
        [
            "--xml-dir",
            str(_stub_corpus),
            "--baseline-cache",
            str(baseline_path),
            "--no-mesh",
            # Anchor phrase guarantees a non-empty hybrid variant even
            # when the 1-paper stub corpus is too thin for LLR to
            # surface a Title/Abstract clause.
            "--query-format",
            "hybrid",
            "--anchor-phrase",
            "cerebral small vessel disease",
            "--validate",
            "--validate-sample",
            "10",
            "--validate-since",
            "2024/01/01",
            "--validate-no-llm-fallback",
            "--validate-output",
            str(validate_dir),
        ]
    )

    assert rc == 0
    # CLI threaded the user-supplied values through to run_validate.
    assert captured["sample_size"] == 10
    assert captured["validate_since"] == "2024/01/01"
    assert captured["use_llm_fallback"] is False
    # The distilled query is non-empty.
    assert captured["query"]
    assert captured["label"].startswith("distilled")
    # The markdown report and JSON sidecar both landed in the output dir.
    md_files = list(validate_dir.glob("*.md"))
    json_files = list(validate_dir.glob("*.json"))
    assert len(md_files) == 1
    assert len(json_files) == 1
    # Stems match — Markdown and JSON refer to the same run.
    assert md_files[0].stem == json_files[0].stem
    # Markdown content carries the validation header.
    md_text = md_files[0].read_text(encoding="utf-8")
    assert "PubMed query relevance validation" in md_text


def test_validate_output_json_path_gets_markdown_sibling(tmp_path: Path) -> None:
    md_path, json_path = _resolve_validate_output_paths(tmp_path / "report.json")

    assert md_path == tmp_path / "report.md"
    assert json_path == tmp_path / "report.json"
    assert md_path != json_path


def test_validate_output_existing_dotted_directory_gets_timestamped_files(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "query.validate"
    output_dir.mkdir()

    md_path, json_path = _resolve_validate_output_paths(output_dir)

    assert md_path.parent == output_dir
    assert json_path.parent == output_dir
    assert md_path.suffix == ".md"
    assert json_path.suffix == ".json"
    assert md_path.stem == json_path.stem


def test_main_missing_baseline_fails(
    _stub_corpus: Path,
    tmp_path: Path,
) -> None:
    rc = main(
        [
            "--xml-dir",
            str(_stub_corpus),
            "--baseline-cache",
            str(tmp_path / "missing.json.gz"),
            "--no-mesh",
            "--json",
            "--output",
            str(tmp_path / "out.json"),
        ]
    )

    assert rc == 1


def test_main_mesh_cache_oserror_degrades_to_no_mesh(
    _stub_corpus: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline_path = tmp_path / "baseline.json.gz"
    _write_baseline_payload(baseline_path)
    mesh_cache_file = tmp_path / "mesh-cache-is-file"
    mesh_cache_file.write_text("not a directory", encoding="utf-8")
    out_path = tmp_path / "out.json"

    rc = main(
        [
            "--xml-dir",
            str(_stub_corpus),
            "--baseline-cache",
            str(baseline_path),
            "--mesh-cache",
            str(mesh_cache_file),
            "--json",
            "--output",
            str(out_path),
        ]
    )

    assert rc == 0
    assert json.loads(out_path.read_text(encoding="utf-8"))["mesh_terms"] == []


def test_main_build_baseline_oserror_returns_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_as_dir = tmp_path / "baseline-output-dir"
    output_as_dir.mkdir()

    def fake_configure(*, email=None, api_key=None):  # noqa: ANN001
        return None

    def fake_retry(fn, *args, _reader, **kwargs):  # noqa: ANN001, ANN002, ANN003
        if getattr(fn, "__name__", "") == "esearch":
            return {"IdList": ["123"]}
        return b"""
        <PubmedArticleSet>
          <PubmedArticle>
            <MedlineCitation>
              <Article>
                <ArticleTitle>Title</ArticleTitle>
                <Abstract><AbstractText>Abstract.</AbstractText></Abstract>
              </Article>
            </MedlineCitation>
          </PubmedArticle>
        </PubmedArticleSet>
        """

    class _FakeEntrez:
        @staticmethod
        def esearch(**_kwargs):  # noqa: ANN202
            return None

        @staticmethod
        def efetch(**_kwargs):  # noqa: ANN202
            return None

        @staticmethod
        def read(_handle):  # noqa: ANN202, ANN001
            return {}

    monkeypatch.setitem(
        __import__("sys").modules, "Bio", type("M", (), {"Entrez": _FakeEntrez})
    )
    monkeypatch.setattr(
        "scripts.distill_pubmed_keywords._configure_entrez", fake_configure
    )
    monkeypatch.setattr("scripts.distill_pubmed_keywords._ncbi_retry", fake_retry)
    monkeypatch.setattr("scripts.distill_pubmed_keywords._ncbi_sleep", lambda _k: None)

    rc = main(
        [
            "--build-baseline",
            "--baseline-size",
            "1",
            "--baseline-cache",
            str(output_as_dir),
        ]
    )

    assert rc == 1


# ---------------------------------------------------------------------------
# Rich rendering — _render_query token styling
# ---------------------------------------------------------------------------


class TestRenderQuery:
    def test_styles_operators_tags_phrases_parens_and_bare(self) -> None:
        # Covers every token class the regex distinguishes:
        # paren, op (AND/OR/NOT), tag (two variants), phrase, bare.
        q = (
            '("vSMC migration"[Title/Abstract] OR Notch3[Title/Abstract] '
            "AND CADASIL[MeSH Terms] NOT mouse)"
        )
        text = _render_query(q)
        # The plain-text projection of the styled Text must equal the
        # input — styling adds visual metadata but never changes bytes.
        assert str(text) == q

        # Bucket the styled spans by style so we can assert each token
        # class landed on the right color.
        spanned: dict[str, set[str]] = {}
        for span in text.spans:
            spanned.setdefault(str(span.style), set()).add(q[span.start : span.end])

        ops = spanned.get("bold magenta", set())
        assert {"AND", "OR", "NOT"}.issubset(ops)

        tags = spanned.get("cyan", set())
        assert {"[Title/Abstract]", "[MeSH Terms]"}.issubset(tags)

        phrases = spanned.get("bold yellow", set())
        assert '"vSMC migration"' in phrases

        parens = spanned.get("dim", set())
        assert {"(", ")"}.issubset(parens)

        # "Bare" tokens are appended with an empty style — rich elides
        # those from Text.spans entirely. Confirm none of them leaked
        # into a styled span (that would mean the regex misclassified
        # them) while still being present in the plain-text projection.
        all_styled = {v for vals in spanned.values() for v in vals}
        for bare_token in ("Notch3", "CADASIL", "mouse"):
            assert bare_token in str(text)
            assert bare_token not in all_styled


# ---------------------------------------------------------------------------
# Rich rendering — _render_rich_report trailing copy-paste line
# ---------------------------------------------------------------------------


def _capture_rich_report(
    result: DistillationResult, *, query_format: str = "all"
) -> str:
    """Render to an in-memory console and return the captured plain text."""
    # `force_terminal=False` + `no_color=True` drops ANSI so the captured
    # bytes are easy to assert against; `record=True` is the rich-native
    # way to grab everything printed during the call.
    console = Console(
        file=None,
        force_terminal=False,
        no_color=True,
        record=True,
        width=200,
        highlight=False,
    )
    _render_rich_report(result, query_format=query_format, console=console)
    return console.export_text()


def test_section_title_does_not_wrap_for_narrow_tables() -> None:
    """When the keyword table auto-sizes narrow (short terms), Rich must
    not wrap the section title across multiple lines — the section name
    and its ``showing N`` count belong on the same line."""
    # Single-char terms force the Term column to minimum width. With
    # the title set on the Table, Rich saw a title wider than the
    # auto-sized table and wrapped it (e.g. "Top distinctive unigrams"
    # on one line, "30" centered below).
    short_terms = [
        KeywordScore(term=t, document_frequency=1, total_count=1, llr=8.42)
        for t in ("a", "b", "c", "d", "e")
    ]
    result = DistillationResult(
        papers=5,
        unigrams=short_terms,
        bigrams=[],
        trigrams=[],
        acronyms=[],
        mesh_terms=[],
    )

    text = _capture_rich_report(result)

    title_lines = [ln for ln in text.splitlines() if "Top distinctive unigrams" in ln]
    assert title_lines, f"Title line not found in output:\n{text}"
    assert "showing 5" in title_lines[0], (
        f"Section title wrapped — title and count landed on separate lines.\n"
        f"Title line: {title_lines[0]!r}\nFull output:\n{text}"
    )


class TestRichReportCopyPaste:
    def test_structured_query_appears_verbatim_at_end(self) -> None:
        # Result has both MeSH terms and phrases, so the structured
        # variant is emitted alongside mesh/titleabstract under
        # query-format=all.
        structured = (
            "(CADASIL[MeSH Terms]) AND "
            '("white matter"[Title/Abstract] OR "small vessel"[Title/Abstract])'
        )
        result = DistillationResult(
            papers=3,
            unigrams=[],
            bigrams=[],
            trigrams=[],
            acronyms=[],
            mesh_terms=[
                KeywordScore(term="CADASIL", document_frequency=2, total_count=4)
            ],
            query_variants={
                "structured": structured,
                "mesh": "CADASIL[MeSH Terms]",
                "titleabstract": (
                    '"white matter"[Title/Abstract] OR "small vessel"[Title/Abstract]'
                ),
            },
        )

        output = _capture_rich_report(result)

        # The plain-text trailer must be present and must be the literal
        # query string — no ANSI escapes, no panel borders interleaved.
        assert "format: structured — copy-paste" in output
        assert structured in output

        # And it must be the last non-empty line so users can grab it
        # without scrolling past more output.
        non_blank_lines = [ln.rstrip() for ln in output.splitlines() if ln.strip()]
        assert non_blank_lines[-1] == structured

    def test_no_trailer_when_structured_format_not_emitted(self) -> None:
        # query-format=mesh limits emission to the mesh panel only;
        # the structured trailer must not appear in that case.
        result = DistillationResult(
            papers=1,
            unigrams=[],
            bigrams=[],
            trigrams=[],
            acronyms=[],
            mesh_terms=[
                KeywordScore(term="CADASIL", document_frequency=1, total_count=2)
            ],
            query_variants={
                "structured": "(CADASIL[MeSH Terms]) AND (x[Title/Abstract])",
                "mesh": "CADASIL[MeSH Terms]",
                "titleabstract": "x[Title/Abstract]",
            },
        )

        output = _capture_rich_report(result, query_format="mesh")

        assert "copy-paste" not in output

    def test_no_trailer_when_structured_variant_empty(self) -> None:
        # No MeSH terms ⇒ structured variant is empty; the trailer
        # has nothing to print and must be suppressed.
        result = DistillationResult(
            papers=1,
            unigrams=[],
            bigrams=[],
            trigrams=[],
            acronyms=[],
            mesh_terms=[],
            query_variants={
                "structured": "",
                "mesh": "",
                "titleabstract": "x[Title/Abstract]",
            },
        )

        output = _capture_rich_report(result)

        assert "copy-paste" not in output


# ---------------------------------------------------------------------------
# Rich rendering — progress_callback wired into load_corpus
# ---------------------------------------------------------------------------


def test_fetch_mesh_terms_invokes_progress_callback(tmp_path: Path) -> None:
    """fetch_mesh_terms should fire the callback both for the cache-hit
    pre-pass and after each subsequent network batch."""
    # Seed one PMID in cache so the pre-pass reports a non-zero
    # ``completed`` before any fetch runs.
    cached_pmid_path = tmp_path / "111.json"
    cached_pmid_path.write_text(
        json.dumps(
            {
                "pmid": "111",
                "descriptors": [{"term": "Brain", "ui": "D001921", "major": True}],
                "fetched_at": "2025-01-01T00:00:00",
            }
        ),
        encoding="utf-8",
    )

    fixture = _FIXTURES.joinpath("mesh_sample.xml").read_bytes()

    def stub_fetcher(_batch: list[str]) -> bytes:
        return fixture

    calls: list[tuple[int, int]] = []
    fetch_mesh_terms(
        ["111", "15905468", "23649698"],
        cache_dir=tmp_path,
        fetcher=stub_fetcher,
        batch_size=2,
        progress_callback=lambda c, t: calls.append((c, t)),
    )

    # First call reports the cache pre-pass (1 of 3 already done).
    assert calls[0] == (1, 3)
    # Final reported completion must be 3/3 (full coverage).
    assert calls[-1] == (3, 3)
    # Monotonic non-decreasing across the run.
    assert all(calls[i][0] <= calls[i + 1][0] for i in range(len(calls) - 1))
    # Total is constant across the run.
    assert {t for _, t in calls} == {3}


def test_fetch_mesh_terms_progress_completes_on_fetch_error(tmp_path: Path) -> None:
    def broken_fetcher(_batch: list[str]) -> bytes:
        raise RuntimeError("simulated NCBI failure")

    calls: list[tuple[int, int]] = []
    out = fetch_mesh_terms(
        ["111", "222"],
        cache_dir=tmp_path,
        fetcher=broken_fetcher,
        batch_size=2,
        progress_callback=lambda c, t: calls.append((c, t)),
    )

    assert out == {}
    assert calls[-1] == (2, 2)


def test_fetch_mesh_terms_progress_completes_on_omitted_pmid(tmp_path: Path) -> None:
    fixture = _FIXTURES.joinpath("mesh_sample.xml").read_bytes()
    calls: list[tuple[int, int]] = []

    out = fetch_mesh_terms(
        ["12345678"],
        cache_dir=tmp_path,
        fetcher=lambda _batch: fixture,
        progress_callback=lambda c, t: calls.append((c, t)),
    )

    assert out == {}
    assert calls[-1] == (1, 1)


def test_load_corpus_invokes_progress_callback_monotonically(
    tmp_path: Path,
) -> None:
    xml_dir = tmp_path / "xml"
    xml_dir.mkdir()
    # Three minimal MODS files. The middle one has no title/abstract so
    # parse_mods_file returns None, but the progress callback should
    # still fire (attempted, not just parsed).
    for i, body in enumerate(
        (
            "<titleInfo><title>One</title></titleInfo><abstract>A1.</abstract>",
            "",  # empty mods body — skipped
            "<titleInfo><title>Three</title></titleInfo><abstract>A3.</abstract>",
        ),
        start=1,
    ):
        (xml_dir / f"{i}.xml").write_text(
            '<?xml version="1.0"?>\n'
            '<modsCollection xmlns="http://www.loc.gov/mods/v3">\n'
            f"  <mods>{body}</mods>\n"
            "</modsCollection>\n",
            encoding="utf-8",
        )

    calls: list[tuple[int, int]] = []
    papers = load_corpus(xml_dir, progress_callback=lambda c, t: calls.append((c, t)))

    assert [c[0] for c in calls] == [1, 2, 3]
    assert all(t == 3 for _, t in calls)
    # 2 of 3 files yielded papers (middle one had no title/abstract).
    assert len(papers) == 2


def test_load_corpus_raises_when_dir_missing(tmp_path: Path) -> None:
    missing = tmp_path / "absent"

    with pytest.raises(FileNotFoundError, match=str(missing)):
        load_corpus(missing)


# ---------------------------------------------------------------------------
# Rich rendering — file outputs stay plain ASCII (no ANSI)
# ---------------------------------------------------------------------------


def test_output_file_contains_no_ansi_escapes(
    _stub_corpus: Path,
    tmp_path: Path,
) -> None:
    baseline_path = tmp_path / "baseline.json.gz"
    _write_baseline_payload(baseline_path)
    out_path = tmp_path / "report.txt"

    rc = main(
        [
            "--xml-dir",
            str(_stub_corpus),
            "--baseline-cache",
            str(baseline_path),
            "--no-mesh",
            "--output",
            str(out_path),
        ]
    )
    assert rc == 0
    content = out_path.read_bytes()
    assert b"\x1b[" not in content, "file output must be ANSI-free"


def test_no_mesh_default_query_output_is_titleabstract_only(
    _stub_corpus: Path,
    tmp_path: Path,
) -> None:
    baseline_path = tmp_path / "baseline.json.gz"
    _write_baseline_payload(baseline_path)
    out_path = tmp_path / "report.txt"

    rc = main(
        [
            "--xml-dir",
            str(_stub_corpus),
            "--baseline-cache",
            str(baseline_path),
            "--no-mesh",
            "--min-df",
            "1",
            "--min-llr",
            "0",
            "--output",
            str(out_path),
        ]
    )

    assert rc == 0
    content = out_path.read_text(encoding="utf-8")
    assert "[titleabstract]" in content
    assert "[structured]" not in content
    assert "[mesh]" not in content


def test_non_tty_stdout_uses_plain_text_report(
    _stub_corpus: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    baseline_path = tmp_path / "baseline.json.gz"
    _write_baseline_payload(baseline_path)

    rc = main(
        [
            "--xml-dir",
            str(_stub_corpus),
            "--baseline-cache",
            str(baseline_path),
            "--no-mesh",
            "--min-df",
            "1",
            "--min-llr",
            "0",
        ]
    )

    captured = capsys.readouterr()
    assert rc == 0
    assert "--- Top distinctive" in captured.out
    assert "┏" not in captured.out
    assert "╭" not in captured.out


def test_json_output_file_contains_no_ansi_escapes(
    _stub_corpus: Path,
    tmp_path: Path,
) -> None:
    baseline_path = tmp_path / "baseline.json.gz"
    _write_baseline_payload(baseline_path)
    out_path = tmp_path / "out.json"

    rc = main(
        [
            "--xml-dir",
            str(_stub_corpus),
            "--baseline-cache",
            str(baseline_path),
            "--no-mesh",
            "--json",
            "--output",
            str(out_path),
        ]
    )
    assert rc == 0
    content = out_path.read_bytes()
    assert b"\x1b[" not in content, "JSON output must be ANSI-free"
    # Sanity check: the file actually parses as JSON with the expected
    # top-level keys, so the byte-stability guarantee is meaningful.
    parsed = json.loads(content.decode("utf-8"))
    assert {"papers", "unigrams", "query_variants"} <= parsed.keys()
