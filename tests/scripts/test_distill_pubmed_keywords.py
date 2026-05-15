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
from rich.console import Console
from scripts.distill_pubmed_keywords import (
    BASELINE_SCHEMA_VERSION,
    FULLTEXT_SCHEMA_VERSION,
    BaselineCounts,
    DistillationResult,
    FulltextRecord,
    KeywordScore,
    MeshDescriptor,
    MeshQualifier,
    PaperText,
    _build_display_map,
    _foreground_acronyms,
    _foreground_counts_for,
    _llr_score,
    _ncbi_retry,
    _non_negative_float,
    _pmcid_from_elink_record,
    _rank_terms,
    _render_query,
    _render_rich_report,
    aggregate_mesh,
    build_baseline_cache,
    distill_keywords,
    fetch_fulltext_batch,
    fetch_mesh_terms,
    format_mesh_query,
    format_structured_query,
    format_titleabstract_query,
    load_baseline_cache,
    load_corpus,
    main,
    parse_jats_for_sections,
    parse_pubmed_xml_for_mesh,
    stem_key,
)

_FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _isolate_xml_extra_default(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop main()-based tests from picking up the real xml_extra dir.

    The CLI default for ``--xml-extra-dir`` resolves to
    ``data/bibentry/xml_extra/`` inside the project, which actually
    exists on developer machines. Without this fixture, every test that
    invokes ``main()`` would inadvertently load whatever XML files live
    there, polluting assertions like ``received_pmids == [["12345"]]``.
    Pointing the constant at a never-created tmp path makes the
    best-effort skip branch in ``main()`` engage.
    """
    nowhere = tmp_path_factory.mktemp("no_xml_extra") / "absent"
    monkeypatch.setattr(
        "scripts.distill_pubmed_keywords.DEFAULT_XML_EXTRA_DIR",
        nowhere,
    )


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

    def test_filtered_surface_forms_cannot_win_modal_display(self) -> None:
        papers = [
            [("result", "results")],
            [("result", "results")],
            [("result", "result")],
        ]

        unfiltered = _build_display_map(papers, 1)
        filtered = _build_display_map(papers, 1, filter_content=True)

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
        fg = Counter({("common",): 10, ("rare",): 990})
        df = Counter({("common",): 1, ("rare",): 5})
        bg = Counter({("common",): 5_000, ("rare",): 2})
        result = _rank_terms(
            fg,
            df,
            bg,
            total_fg=1_000,
            total_bg=10_000,
            min_df=1,
            top_n=10,
            min_llr=0.0,
        )
        terms = [r.term for r in result]
        assert "rare" in terms
        assert "common" not in terms

    def test_fallback_to_df_when_no_baseline(self) -> None:
        fg = Counter({("x",): 5, ("y",): 2})
        df = Counter({("x",): 3, ("y",): 2})
        result = _rank_terms(
            fg,
            df,
            None,
            total_fg=7,
            total_bg=None,
            min_df=1,
            top_n=10,
            min_llr=0.0,
        )
        assert result[0].term == "x"
        assert result[0].llr == 0.0  # no LLR computed in DF fallback

    def test_min_df_threshold(self) -> None:
        fg = Counter({("kept",): 3, ("dropped",): 1})
        df = Counter({("kept",): 2, ("dropped",): 1})
        bg = Counter({("kept",): 0, ("dropped",): 0})
        result = _rank_terms(
            fg,
            df,
            bg,
            total_fg=4,
            total_bg=1_000,
            min_df=2,
            top_n=10,
            min_llr=0.0,
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
            fg,
            df,
            bg,
            total_fg=3,
            total_bg=1_000,
            min_df=1,
            top_n=10,
            min_llr=0.0,
            display=display,
        )
        assert result[0].term == "GENES"

    def test_non_positive_top_n_returns_empty(self) -> None:
        fg = Counter({("gene",): 3})
        df = Counter({("gene",): 2})
        bg = Counter({("gene",): 1})

        assert (
            _rank_terms(
                fg,
                df,
                bg,
                total_fg=3,
                total_bg=1_000,
                min_df=1,
                top_n=0,
                min_llr=0.0,
            )
            == []
        )
        assert (
            _rank_terms(
                fg,
                df,
                None,
                total_fg=3,
                total_bg=None,
                min_df=1,
                top_n=-1,
                min_llr=0.0,
            )
            == []
        )

    @pytest.mark.parametrize("bad_min_llr", [float("nan"), float("inf"), -0.1])
    def test_bad_min_llr_raises(self, bad_min_llr: float) -> None:
        with pytest.raises(ValueError, match="min_llr"):
            _rank_terms(
                Counter({("gene",): 3}),
                Counter({("gene",): 2}),
                Counter({("gene",): 0}),
                total_fg=3,
                total_bg=1_000,
                min_df=1,
                top_n=10,
                min_llr=bad_min_llr,
            )


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


# ---------------------------------------------------------------------------
# Baseline cache I/O
# ---------------------------------------------------------------------------


def _write_baseline_payload(
    path: Path, *, built_at: str, override: dict | None = None
) -> None:
    payload = {
        "schema_version": BASELINE_SCHEMA_VERSION,
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
        # Mirrors test_pmid_absent_from_elink_response_is_not_cached for
        # the fulltext path.
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


def _write_mods(
    path: Path, *, pmid: str, title: str = "T", abstract: str = "A."
) -> None:
    """Write a minimal MODS XML record with the given PMID, title, and abstract."""
    path.write_text(
        '<?xml version="1.0"?>\n'
        '<modsCollection xmlns="http://www.loc.gov/mods/v3">\n'
        "  <mods>\n"
        f"    <titleInfo><title>{title}</title></titleInfo>\n"
        f"    <abstract>{abstract}</abstract>\n"
        f'    <identifier type="pubmed">{pmid}</identifier>\n'
        "  </mods>\n"
        "</modsCollection>\n",
        encoding="utf-8",
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

    def test_structured_abstract_labels_are_not_acronyms(self) -> None:
        papers = [
            PaperText(
                pmid="1",
                title="FINDINGS and FUNDING",
                abstract="WMH was measured by MRI.",
            )
        ]

        tf, df = _foreground_acronyms(papers)

        assert "FINDINGS" not in tf
        assert "FUNDING" not in tf
        assert "FINDINGS" not in df
        assert "FUNDING" not in df
        assert tf["WMH"] == 1
        assert tf["MRI"] == 1

    def test_fulltext_artifact_labels_are_filtered_from_phrases(self) -> None:
        pairs = [
            [
                ("supplementary", "supplementary"),
                ("table", "table"),
                ("white", "white"),
                ("matter", "matter"),
            ]
        ]

        tf, _ = _foreground_counts_for(pairs, n=2, filter_content=True)

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

        tf, _ = _foreground_counts_for(pairs, n=3, filter_content=True)

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
            schema_version=BASELINE_SCHEMA_VERSION,
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
            schema_version=BASELINE_SCHEMA_VERSION,
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

    def test_descends_into_unclassified_outer_sec(self) -> None:
        # Some publishers wrap IMRaD content in an outer unnamed
        # <sec><title>Main Text</title>...</sec>. The parser must descend
        # into the wrapper rather than dropping the entire subtree.
        xml_bytes = (_FIXTURES / "jats_nested_wrapper.xml").read_bytes()
        sections = parse_jats_for_sections(xml_bytes)
        assert "small vessel disease" in sections["introduction"].lower()
        assert "magnetic resonance imaging" in sections["methods"].lower()
        assert "white matter hyperintensity" in sections["results"].lower()
        assert "prospective" in sections["discussion"].lower()
        # The Acknowledgments sibling outside the wrapper must still be
        # dropped (no IMRaD classification, no nested IMRaD inside it).
        combined = " ".join(sections.values()).lower()
        assert "acknowledgments section should still be dropped" not in combined

    def test_nested_classified_section_overrides_parent_label(self) -> None:
        xml = b"""
        <article>
          <body>
            <sec sec-type="introduction">
              <title>Introduction</title>
              <p>Introductory small vessel disease context.</p>
              <sec sec-type="methods">
                <title>Methods</title>
                <p>Nested imaging protocol text.</p>
              </sec>
            </sec>
          </body>
        </article>
        """
        sections = parse_jats_for_sections(xml)

        assert "introductory small vessel disease" in sections["introduction"].lower()
        assert "nested imaging protocol" not in sections["introduction"].lower()
        assert "nested imaging protocol" in sections["methods"].lower()

    def test_unclassified_subsection_inherits_parent_label(self) -> None:
        xml = b"""
        <article>
          <body>
            <sec sec-type=" methods ">
              <title>Methods</title>
              <p>Parent protocol text.</p>
              <sec>
                <title>Participants</title>
                <p>Inherited participant details.</p>
              </sec>
            </sec>
          </body>
        </article>
        """
        sections = parse_jats_for_sections(xml)

        assert "parent protocol text" in sections["methods"].lower()
        assert "inherited participant details" in sections["methods"].lower()

    def test_paragraphs_inside_non_section_containers_are_kept(self) -> None:
        xml = b"""
        <article>
          <body>
            <sec sec-type="methods">
              <title>Methods</title>
              <list>
                <list-item>
                  <p>List-based acquisition protocol details.</p>
                </list-item>
              </list>
              <sec sec-type="results">
                <title>Results</title>
                <p>Nested result text.</p>
              </sec>
            </sec>
          </body>
        </article>
        """
        sections = parse_jats_for_sections(xml)

        assert "list-based acquisition protocol" in sections["methods"].lower()
        assert "nested result text" not in sections["methods"].lower()
        assert "nested result text" in sections["results"].lower()

    def test_display_object_paragraphs_are_skipped(self) -> None:
        xml = b"""
        <article>
          <body>
            <sec sec-type="results">
              <title>Results</title>
              <p>Prose result about white matter injury.</p>
              <table-wrap>
                <caption><p>Supplementary table caption should be ignored.</p></caption>
                <table>
                  <tbody>
                    <tr><td><p>Table cell boilerplate should be ignored.</p></td></tr>
                  </tbody>
                </table>
              </table-wrap>
              <fig>
                <caption><p>Figure caption should be ignored.</p></caption>
              </fig>
            </sec>
          </body>
        </article>
        """

        sections = parse_jats_for_sections(xml)

        assert "prose result" in sections["results"].lower()
        assert "supplementary table caption" not in sections["results"].lower()
        assert "table cell boilerplate" not in sections["results"].lower()
        assert "figure caption" not in sections["results"].lower()

    def test_body_root_is_parsed(self) -> None:
        xml = b"""
        <body>
          <sec sec-type="discussion">
            <title>Discussion</title>
            <p>Standalone body root text.</p>
          </sec>
        </body>
        """
        sections = parse_jats_for_sections(xml)

        assert "standalone body root text" in sections["discussion"].lower()


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

    def test_normalizes_numeric_pmcid_before_fetch_and_cache(
        self, tmp_path: Path
    ) -> None:
        jats_bytes = (_FIXTURES / "jats_sample.xml").read_bytes()
        efetch_calls: list[str] = []

        def stub_elink(batch: list[str]) -> dict[str, str | None]:
            return dict.fromkeys(batch, "1234567")

        def stub_efetch(pmcid: str) -> bytes | None:
            efetch_calls.append(pmcid)
            return jats_bytes

        out = fetch_fulltext_batch(
            ["15905468"],
            tmp_path,
            fetcher_elink=stub_elink,
            fetcher_efetch=stub_efetch,
        )

        assert efetch_calls == ["PMC1234567"]
        assert out["15905468"].pmcid == "PMC1234567"
        payload = json.loads((tmp_path / "15905468.json").read_text(encoding="utf-8"))
        assert payload["pmcid"] == "PMC1234567"

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

    def test_skips_on_malformed_elink_payload(self, tmp_path: Path) -> None:
        calls: list[tuple[int, int]] = []

        out = fetch_fulltext_batch(
            ["123", "456"],
            tmp_path,
            fetcher_elink=lambda _batch: None,
            fetcher_efetch=lambda _pmcid: None,
            progress_callback=lambda c, t: calls.append((c, t)),
        )

        assert out == {}
        assert calls[-1] == (2, 2)
        assert not (tmp_path / "123.json").exists()
        assert not (tmp_path / "456.json").exists()

    def test_accepts_integer_elink_mapping_keys(self, tmp_path: Path) -> None:
        jats_bytes = (_FIXTURES / "jats_sample.xml").read_bytes()

        out = fetch_fulltext_batch(
            ["123"],
            tmp_path,
            fetcher_elink=lambda _batch: {123: "PMC1234567"},
            fetcher_efetch=lambda _pmcid: jats_bytes,
        )

        assert out["123"].pmcid == "PMC1234567"
        assert (tmp_path / "123.json").exists()

    def test_skips_malformed_pmcid_without_negative_cache(self, tmp_path: Path) -> None:
        efetch_calls: list[str] = []

        out = fetch_fulltext_batch(
            ["123"],
            tmp_path,
            fetcher_elink=lambda _batch: {"123": "PMC../escape"},
            fetcher_efetch=lambda pmcid: efetch_calls.append(pmcid) or None,
        )

        assert out == {}
        assert efetch_calls == []
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

    def test_batches_elink_for_multiple_missing_pmids(self, tmp_path: Path) -> None:
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

    def test_refetches_on_missing_sections_mapping(self, tmp_path: Path) -> None:
        (tmp_path / "777.json").write_text(
            json.dumps(
                {
                    "schema_version": FULLTEXT_SCHEMA_VERSION,
                    "pmid": "777",
                    "pmcid": "PMC777",
                    "fetched_at": "2025-01-01T00:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )

        jats_bytes = (_FIXTURES / "jats_sample.xml").read_bytes()
        elink_calls: list[list[str]] = []

        def stub_elink(batch: list[str]) -> dict[str, str | None]:
            elink_calls.append(list(batch))
            return dict.fromkeys(batch, "PMC777")

        out = fetch_fulltext_batch(
            ["777"],
            tmp_path,
            fetcher_elink=stub_elink,
            fetcher_efetch=lambda _pmcid: jats_bytes,
        )

        assert elink_calls == [["777"]]
        assert "small vessel disease" in out["777"].introduction.lower()

    def test_refetches_on_non_string_cached_section(self, tmp_path: Path) -> None:
        (tmp_path / "777.json").write_text(
            json.dumps(
                {
                    "schema_version": FULLTEXT_SCHEMA_VERSION,
                    "pmid": "777",
                    "pmcid": "PMC777",
                    "sections": {"introduction": ["not", "text"]},
                    "fetched_at": "2025-01-01T00:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )

        jats_bytes = (_FIXTURES / "jats_sample.xml").read_bytes()
        elink_calls: list[list[str]] = []

        def stub_elink(batch: list[str]) -> dict[str, str | None]:
            elink_calls.append(list(batch))
            return dict.fromkeys(batch, "PMC777")

        out = fetch_fulltext_batch(
            ["777"],
            tmp_path,
            fetcher_elink=stub_elink,
            fetcher_efetch=lambda _pmcid: jats_bytes,
        )

        assert elink_calls == [["777"]]
        assert "not', 'text" not in out["777"].as_text()
        assert "small vessel disease" in out["777"].introduction.lower()

    def test_refetches_on_malformed_cached_pmcid(self, tmp_path: Path) -> None:
        (tmp_path / "777.json").write_text(
            json.dumps(
                {
                    "schema_version": FULLTEXT_SCHEMA_VERSION,
                    "pmid": "777",
                    "pmcid": "PMC../escape",
                    "sections": {"introduction": "stale"},
                    "fetched_at": "2025-01-01T00:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )

        jats_bytes = (_FIXTURES / "jats_sample.xml").read_bytes()
        elink_calls: list[list[str]] = []

        def stub_elink(batch: list[str]) -> dict[str, str | None]:
            elink_calls.append(list(batch))
            return dict.fromkeys(batch, "PMC777")

        out = fetch_fulltext_batch(
            ["777"],
            tmp_path,
            fetcher_elink=stub_elink,
            fetcher_efetch=lambda _pmcid: jats_bytes,
        )

        assert elink_calls == [["777"]]
        assert out["777"].pmcid == "PMC777"
        assert out["777"].introduction != "stale"

    def test_refetches_on_negative_cache_with_section_text(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "777.json").write_text(
            json.dumps(
                {
                    "schema_version": FULLTEXT_SCHEMA_VERSION,
                    "pmid": "777",
                    "pmcid": None,
                    "sections": {"introduction": "impossible cached text"},
                    "fetched_at": "2025-01-01T00:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )

        jats_bytes = (_FIXTURES / "jats_sample.xml").read_bytes()
        elink_calls: list[list[str]] = []

        def stub_elink(batch: list[str]) -> dict[str, str | None]:
            elink_calls.append(list(batch))
            return dict.fromkeys(batch, "PMC777")

        out = fetch_fulltext_batch(
            ["777"],
            tmp_path,
            fetcher_elink=stub_elink,
            fetcher_efetch=lambda _pmcid: jats_bytes,
        )

        assert elink_calls == [["777"]]
        assert out["777"].pmcid == "PMC777"
        assert out["777"].introduction != "impossible cached text"

    def test_accepts_string_efetch_response(self, tmp_path: Path) -> None:
        jats_text = (_FIXTURES / "jats_sample.xml").read_text(encoding="utf-8")

        out = fetch_fulltext_batch(
            ["15905468"],
            tmp_path,
            fetcher_elink=lambda batch: dict.fromkeys(batch, "PMC1234567"),
            fetcher_efetch=lambda _pmcid: jats_text,
        )

        assert "small vessel disease" in out["15905468"].introduction.lower()

    def test_pmid_absent_from_elink_response_is_not_cached(
        self, tmp_path: Path
    ) -> None:
        # If NCBI's elink response omits a PMID (truncation, partial
        # response), the caller must not write a permanent negative
        # cache entry for it — that would silently drop the paper's
        # full-text contribution on every future run with no recovery.
        # Absent-from-map encodes "NCBI didn't answer"; None encodes
        # "NCBI confirmed no PMC mirror".
        def partial_elink(batch: list[str]) -> dict[str, str | None]:
            # Only the first PMID gets a response; the second is silently
            # dropped, simulating a truncated NCBI reply.
            return {batch[0]: None}

        def unused_efetch(_pmcid: str) -> bytes | None:
            raise AssertionError("efetch should not run for any PMID here")

        out = fetch_fulltext_batch(
            ["111", "222"],
            tmp_path,
            fetcher_elink=partial_elink,
            fetcher_efetch=unused_efetch,
        )
        # PMID 111 had a confirmed no-PMC response → negative cache.
        assert out["111"].pmcid is None
        assert (tmp_path / "111.json").exists()
        # PMID 222 was absent from the response → no cache, retry next run.
        assert "222" not in out
        assert not (tmp_path / "222.json").exists()

    def test_refuses_non_numeric_pmids_before_fetch(self, tmp_path: Path) -> None:
        elink_calls: list[list[str]] = []

        def fail_elink(batch: list[str]) -> dict[str, str | None]:
            elink_calls.append(batch)
            raise AssertionError("invalid PMID must not reach elink")

        out = fetch_fulltext_batch(
            ["../escape"],
            tmp_path,
            fetcher_elink=fail_elink,
            fetcher_efetch=lambda _pmcid: None,
        )

        assert out == {}
        assert elink_calls == []
        assert not (tmp_path.parent / "escape.json").exists()

    def test_duplicate_pmids_are_deduped_before_elink(self, tmp_path: Path) -> None:
        jats_bytes = (_FIXTURES / "jats_sample.xml").read_bytes()
        elink_calls: list[list[str]] = []
        progress: list[tuple[int, int]] = []

        def stub_elink(batch: list[str]) -> dict[str, str | None]:
            elink_calls.append(list(batch))
            return dict.fromkeys(batch, "PMC1")

        out = fetch_fulltext_batch(
            ["15905468", "15905468"],
            tmp_path,
            fetcher_elink=stub_elink,
            fetcher_efetch=lambda _pmcid: jats_bytes,
            progress_callback=lambda c, t: progress.append((c, t)),
        )

        assert elink_calls == [["15905468"]]
        assert set(out) == {"15905468"}
        assert progress[-1] == (1, 1)

    def test_cache_pmid_mismatch_is_refetched(self, tmp_path: Path) -> None:
        _write_cached_record(
            tmp_path,
            FulltextRecord(
                pmid="222",
                pmcid="PMC222",
                introduction="wrong cached intro",
            ),
        )
        # Move the mismatched payload under the requested PMID filename.
        (tmp_path / "111.json").write_text(
            (tmp_path / "222.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )

        jats_bytes = (_FIXTURES / "jats_sample.xml").read_bytes()
        elink_calls: list[list[str]] = []

        def stub_elink(batch: list[str]) -> dict[str, str | None]:
            elink_calls.append(list(batch))
            return dict.fromkeys(batch, "PMC111")

        out = fetch_fulltext_batch(
            ["111"],
            tmp_path,
            fetcher_elink=stub_elink,
            fetcher_efetch=lambda _pmcid: jats_bytes,
        )

        assert elink_calls == [["111"]]
        assert out["111"].pmid == "111"
        assert out["111"].introduction != "wrong cached intro"


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


class TestDefaultPmidsToPmcids:
    def test_pmcid_from_elink_record_ignores_non_pmc_links(self) -> None:
        record = {
            "LinkSetDb": [
                {
                    "DbTo": "pubmed",
                    "LinkName": "pubmed_pubmed",
                    "Link": [{"Id": "999"}],
                },
                {
                    "DbTo": "pmc",
                    "LinkName": "pubmed_pmc",
                    "Link": [{"Id": "1234567"}],
                },
            ]
        }

        assert _pmcid_from_elink_record(record) == "PMC1234567"

    def test_pmcid_from_elink_record_ignores_malformed_link_ids(self) -> None:
        record = {
            "LinkSetDb": [
                {
                    "DbTo": "pmc",
                    "LinkName": "pubmed_pmc",
                    "Link": [{"Id": "PMC../escape"}, {"Id": "../escape"}],
                }
            ]
        }

        assert _pmcid_from_elink_record(record) is None

    def test_pmcid_from_elink_record_requires_pmc_link_metadata(self) -> None:
        record = {
            "LinkSetDb": [
                {
                    "Link": [{"Id": "1234567"}],
                }
            ]
        }

        assert _pmcid_from_elink_record(record) is None

    def test_parses_pmcid_from_linkset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Per-PMID dispatch is deliberate — see the function's docstring
        # for the NCBI chunked-response bug that forced it.
        import scripts.distill_pubmed_keywords as mod

        class _FakeHandle:
            def __init__(self, pmid: str) -> None:
                self.pmid = pmid

            def close(self) -> None:
                pass

        per_pmid_records: dict[str, list[dict]] = {
            "111": [
                {
                    "IdList": ["111"],
                    "LinkSetDb": [
                        {
                            "DbTo": "pmc",
                            "LinkName": "pubmed_pmc",
                            "Link": [{"Id": "1234567"}],
                        }
                    ],
                }
            ],
            "222": [
                {
                    "IdList": ["222"],
                    "LinkSetDb": [],  # confirmed no PMC mirror
                }
            ],
            # PMID "333" intentionally returns no record (truncated reply
            # / NCBI omission) — must end up absent from the output map.
            "333": [],
        }
        elink_calls: list[str] = []

        class _FakeEntrez:
            @staticmethod
            def elink(**kwargs: object) -> _FakeHandle:
                pmid = str(kwargs.get("id"))
                elink_calls.append(pmid)
                return _FakeHandle(pmid)

            @staticmethod
            def read(handle: _FakeHandle) -> list[dict]:
                return per_pmid_records[handle.pmid]

        monkeypatch.setitem(
            __import__("sys").modules, "Bio", type("M", (), {"Entrez": _FakeEntrez})
        )
        # _ncbi_sleep is a no-op for tests
        monkeypatch.setattr(mod, "_ncbi_sleep", lambda _k: None)

        out = mod._default_pmids_to_pmcids(["111", "222", "333"], api_key=None)
        assert out["111"] == "PMC1234567"
        assert out["222"] is None
        # PMID 333 must be absent (not pre-filled with None) so the
        # caller can retry it instead of negative-caching forever.
        assert "333" not in out
        # Per-PMID dispatch: one elink call per input PMID.
        assert elink_calls == ["111", "222", "333"]


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
        "A study of vessels We measured things. Body text covering Methods and Results."
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
    _write_mods(
        xml_dir / "1.xml", pmid="12345", title="Stub title", abstract="Stub abstract."
    )
    return xml_dir


def _write_minimal_baseline(path: Path) -> None:
    """Write a gzip baseline cache `load_baseline_cache` will accept."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": BASELINE_SCHEMA_VERSION,
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

    monkeypatch.setattr("scripts.distill_pubmed_keywords.fetch_fulltext_batch", spy)

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

    monkeypatch.setattr("scripts.distill_pubmed_keywords.fetch_fulltext_batch", spy)

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


def test_main_xml_extra_dir_contributes_to_corpus(
    _stub_corpus: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit --xml-extra-dir adds its PMIDs to the run, alongside --xml-dir."""
    baseline_path = tmp_path / "baseline.json.gz"
    _write_minimal_baseline(baseline_path)
    fulltext_cache = tmp_path / "ft"

    extra_dir = tmp_path / "xml_extra"
    extra_dir.mkdir()
    _write_mods(
        extra_dir / "extra.xml",
        pmid="67890",
        title="Extra title",
        abstract="Extra abstract.",
    )

    received_pmids: list[list[str]] = []

    def spy(pmids, cache_dir, **_kwargs):  # noqa: ANN001, ANN003
        received_pmids.append(list(pmids))
        return {}

    monkeypatch.setattr("scripts.distill_pubmed_keywords.fetch_fulltext_batch", spy)

    out_path = tmp_path / "out.json"
    rc = main(
        [
            "--xml-dir",
            str(_stub_corpus),
            "--xml-extra-dir",
            str(extra_dir),
            "--baseline-cache",
            str(baseline_path),
            "--fulltext-cache",
            str(fulltext_cache),
            "--no-mesh",
            "--json",
            "--output",
            str(out_path),
        ]
    )

    assert rc == 0
    assert len(received_pmids) == 1
    assert set(received_pmids[0]) == {"12345", "67890"}
    assert json.loads(out_path.read_text(encoding="utf-8"))["papers"] == 2


def test_main_missing_baseline_fails_before_fulltext_fetch(
    _stub_corpus: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple, dict]] = []

    def spy(*args, **kwargs):  # noqa: ANN002, ANN003
        calls.append((args, kwargs))
        return {}

    monkeypatch.setattr("scripts.distill_pubmed_keywords.fetch_fulltext_batch", spy)

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
    assert calls == []


def test_main_fulltext_cache_oserror_degrades_to_title_abstract(
    _stub_corpus: Path,
    tmp_path: Path,
) -> None:
    baseline_path = tmp_path / "baseline.json.gz"
    _write_minimal_baseline(baseline_path)
    fulltext_cache_file = tmp_path / "fulltext-cache-is-file"
    fulltext_cache_file.write_text("not a directory", encoding="utf-8")
    out_path = tmp_path / "out.json"

    rc = main(
        [
            "--xml-dir",
            str(_stub_corpus),
            "--baseline-cache",
            str(baseline_path),
            "--fulltext-cache",
            str(fulltext_cache_file),
            "--no-mesh",
            "--json",
            "--output",
            str(out_path),
        ]
    )

    assert rc == 0
    assert json.loads(out_path.read_text(encoding="utf-8"))["papers"] == 1


def test_main_mesh_cache_oserror_degrades_to_no_mesh(
    _stub_corpus: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline_path = tmp_path / "baseline.json.gz"
    _write_minimal_baseline(baseline_path)
    mesh_cache_file = tmp_path / "mesh-cache-is-file"
    mesh_cache_file.write_text("not a directory", encoding="utf-8")
    out_path = tmp_path / "out.json"

    monkeypatch.setattr(
        "scripts.distill_pubmed_keywords.fetch_fulltext_batch", lambda *a, **k: {}
    )

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
                    '"white matter"[Title/Abstract] OR '
                    '"small vessel"[Title/Abstract]'
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


def test_fetch_fulltext_batch_invokes_progress_callback(tmp_path: Path) -> None:
    """fetch_fulltext_batch should fire the callback once per PMID
    iteration plus once for the cache-hit pre-pass."""
    jats_bytes = (_FIXTURES / "jats_sample.xml").read_bytes()

    def stub_elink(batch: list[str]) -> dict[str, str | None]:
        # First PMID resolves to a PMC mirror; second has no mirror;
        # third missing from response — exercise all three code paths.
        return {"15905468": "PMC1234567", "23649698": None}

    def stub_efetch(_pmcid: str) -> bytes | None:
        return jats_bytes

    calls: list[tuple[int, int]] = []
    fetch_fulltext_batch(
        ["15905468", "23649698", "99999999"],
        tmp_path,
        fetcher_elink=stub_elink,
        fetcher_efetch=stub_efetch,
        progress_callback=lambda c, t: calls.append((c, t)),
    )

    # Pre-pass: nothing cached yet, so the cache-hit count is 0.
    assert calls[0] == (0, 3)
    # Final completion must equal total — the try/finally ensures the
    # callback ticks even when the PMID is dropped from elink.
    assert calls[-1] == (3, 3)
    assert all(calls[i][0] <= calls[i + 1][0] for i in range(len(calls) - 1))
    assert {t for _, t in calls} == {3}


def test_fetch_fulltext_batch_progress_completes_on_elink_error(
    tmp_path: Path,
) -> None:
    def broken_elink(_batch: list[str]) -> dict[str, str | None]:
        raise RuntimeError("simulated NCBI failure")

    calls: list[tuple[int, int]] = []
    out = fetch_fulltext_batch(
        ["111", "222"],
        tmp_path,
        fetcher_elink=broken_elink,
        fetcher_efetch=lambda _pmcid: None,
        progress_callback=lambda c, t: calls.append((c, t)),
    )

    assert out == {}
    assert calls[-1] == (2, 2)


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
    papers = load_corpus([xml_dir], progress_callback=lambda c, t: calls.append((c, t)))

    assert [c[0] for c in calls] == [1, 2, 3]
    assert all(t == 3 for _, t in calls)
    # 2 of 3 files yielded papers (middle one had no title/abstract).
    assert len(papers) == 2


def test_load_corpus_merges_multiple_dirs(tmp_path: Path) -> None:
    """Files from the supplementary dir reach the corpus alongside the primary."""
    primary = tmp_path / "xml"
    extra = tmp_path / "xml_extra"
    primary.mkdir()
    extra.mkdir()

    _write_mods(primary / "1.xml", pmid="1001", title="Primary paper")
    _write_mods(extra / "2.xml", pmid="2002", title="Extra paper")

    papers = load_corpus([primary, extra])

    pmids = {p.pmid for p in papers}
    assert pmids == {"1001", "2002"}


def test_load_corpus_raises_when_any_dir_missing(tmp_path: Path) -> None:
    """All dirs in the iterable must exist — the function raises on the first miss."""
    primary = tmp_path / "xml"
    primary.mkdir()
    missing = tmp_path / "absent"

    with pytest.raises(FileNotFoundError, match=str(missing)):
        load_corpus([primary, missing])


# ---------------------------------------------------------------------------
# Rich rendering — file outputs stay plain ASCII (no ANSI)
# ---------------------------------------------------------------------------


def test_output_file_contains_no_ansi_escapes(
    _stub_corpus: Path,
    tmp_path: Path,
) -> None:
    baseline_path = tmp_path / "baseline.json.gz"
    _write_minimal_baseline(baseline_path)
    out_path = tmp_path / "report.txt"

    rc = main(
        [
            "--xml-dir",
            str(_stub_corpus),
            "--baseline-cache",
            str(baseline_path),
            "--no-fulltext",
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
    _write_minimal_baseline(baseline_path)
    out_path = tmp_path / "report.txt"

    rc = main(
        [
            "--xml-dir",
            str(_stub_corpus),
            "--baseline-cache",
            str(baseline_path),
            "--no-fulltext",
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


def test_json_output_file_contains_no_ansi_escapes(
    _stub_corpus: Path,
    tmp_path: Path,
) -> None:
    baseline_path = tmp_path / "baseline.json.gz"
    _write_minimal_baseline(baseline_path)
    out_path = tmp_path / "out.json"

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
