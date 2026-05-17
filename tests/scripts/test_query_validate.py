"""Unit tests for scripts/_query_validate.py.

Network paths (esearch, efetch) and the Anthropic API are exercised
through injectable callables so the suite runs fully offline.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from scripts._query_validate import (
    _RELEVANT_MESH_FLOOR,
    LlmRelevanceScorer,
    LlmVerdict,
    PaperRecord,
    QueryValidation,
    RecallFloor,
    RelevanceScore,
    ValidationReport,
    _parse_llm_response,
    compute_recall_floor,
    derive_relevant_mesh_set,
    efetch_papers_with_abstract,
    emit_validate_json,
    load_bibliography_gold_pmids,
    parse_pubmed_xml_for_records,
    render_validate_report,
    run_validate,
    sample_pmids,
    score_relevance,
)

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_RELEVANCE_XML = (_FIXTURES_DIR / "relevance_efetch_sample.xml").read_bytes()


# ---------------------------------------------------------------------------
# derive_relevant_mesh_set
# ---------------------------------------------------------------------------


def _write_mesh_json(path: Path, pmid: str, terms: list[str]) -> None:
    payload = {
        "pmid": pmid,
        "descriptors": [
            {"term": t, "ui": f"D{i:06d}", "major": False, "qualifiers": []}
            for i, t in enumerate(terms)
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


class TestDeriveRelevantMeshSet:
    def test_threshold_keeps_terms_above_min_papers(self, tmp_path: Path) -> None:
        _write_mesh_json(tmp_path / "1.json", "1", ["Stroke", "Foo"])
        _write_mesh_json(tmp_path / "2.json", "2", ["Stroke", "Bar"])
        _write_mesh_json(tmp_path / "3.json", "3", ["Stroke", "Baz"])
        out = derive_relevant_mesh_set(tmp_path, min_papers=3, floor=())
        assert "Stroke" in out
        # Foo / Bar / Baz appear in only one paper — below threshold.
        assert "Foo" not in out
        assert "Bar" not in out
        assert "Baz" not in out

    def test_population_stopwords_excluded(self, tmp_path: Path) -> None:
        # All five papers tagged Humans + Adult + Female; raw counts
        # are high enough to clear the threshold, but those terms
        # should be filtered out as population metadata.
        for i in range(5):
            _write_mesh_json(
                tmp_path / f"{i}.json",
                str(i),
                ["Humans", "Adult", "Female", "Leukoencephalopathies"],
            )
        out = derive_relevant_mesh_set(tmp_path, min_papers=3, floor=())
        assert "Humans" not in out
        assert "Adult" not in out
        assert "Female" not in out
        assert "Leukoencephalopathies" in out

    def test_floor_unioned_in_even_when_absent(self, tmp_path: Path) -> None:
        # Bibliography happens to not include the canonical headings —
        # the floor should still appear in the output.
        _write_mesh_json(tmp_path / "1.json", "1", ["Stroke"])
        _write_mesh_json(tmp_path / "2.json", "2", ["Stroke"])
        _write_mesh_json(tmp_path / "3.json", "3", ["Stroke"])
        out = derive_relevant_mesh_set(
            tmp_path, min_papers=3, floor=["CADASIL", "Leukoaraiosis"]
        )
        assert "CADASIL" in out
        assert "Leukoaraiosis" in out

    def test_default_floor_is_used(self, tmp_path: Path) -> None:
        _write_mesh_json(tmp_path / "1.json", "1", ["Stroke"])
        _write_mesh_json(tmp_path / "2.json", "2", ["Stroke"])
        _write_mesh_json(tmp_path / "3.json", "3", ["Stroke"])
        out = derive_relevant_mesh_set(tmp_path, min_papers=3)
        # Default floor includes Cerebral Small Vessel Diseases, CADASIL, etc.
        assert "Cerebral Small Vessel Diseases" in out
        assert "CADASIL" in out
        # And the empirical threshold gives us Stroke.
        assert "Stroke" in out

    def test_term_repeated_within_paper_counts_once(self, tmp_path: Path) -> None:
        # Two duplicates of the same term in the same paper should not
        # multiply that term's count.
        payload = {
            "pmid": "1",
            "descriptors": [
                {"term": "Stroke", "ui": "D1", "major": False, "qualifiers": []},
                {"term": "Stroke", "ui": "D1", "major": True, "qualifiers": []},
            ],
        }
        (tmp_path / "1.json").write_text(json.dumps(payload), encoding="utf-8")
        _write_mesh_json(tmp_path / "2.json", "2", ["Foo"])
        out = derive_relevant_mesh_set(tmp_path, min_papers=2, floor=())
        # Stroke appears in only one paper, so should NOT be in the set.
        assert "Stroke" not in out

    def test_missing_dir_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            derive_relevant_mesh_set(tmp_path / "does-not-exist")

    def test_empty_dir_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            derive_relevant_mesh_set(tmp_path)

    def test_corrupt_file_skipped(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        (tmp_path / "bad.json").write_text("not valid json", encoding="utf-8")
        _write_mesh_json(tmp_path / "ok1.json", "1", ["Leukoencephalopathies"])
        _write_mesh_json(tmp_path / "ok2.json", "2", ["Leukoencephalopathies"])
        with caplog.at_level("WARNING"):
            out = derive_relevant_mesh_set(tmp_path, min_papers=2, floor=())
        assert "Leukoencephalopathies" in out
        assert any("corrupt" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# parse_pubmed_xml_for_records — abstract extraction
# ---------------------------------------------------------------------------


class TestParsePubmedXmlForRecords:
    def test_extracts_abstract_segments(self) -> None:
        out = parse_pubmed_xml_for_records(_RELEVANCE_XML)
        assert "11111" in out
        record = out["11111"]
        assert "CADASIL is a hereditary" in record.abstract
        assert "NOTCH3 mutations" in record.abstract
        assert "extensive white matter" in record.abstract

    def test_paper_without_mesh_still_parsed(self) -> None:
        out = parse_pubmed_xml_for_records(_RELEVANCE_XML)
        assert "33333" in out
        assert out["33333"].mesh == []
        assert "lacunar stroke" in out["33333"].abstract

    def test_mesh_headings_extracted(self) -> None:
        out = parse_pubmed_xml_for_records(_RELEVANCE_XML)
        record = out["11111"]
        assert "CADASIL" in record.mesh
        assert "Humans" in record.mesh

    def test_journal_and_year(self) -> None:
        out = parse_pubmed_xml_for_records(_RELEVANCE_XML)
        assert out["11111"].journal == "Journal of cSVD Research"
        assert out["11111"].year == "2022"


# ---------------------------------------------------------------------------
# efetch_papers_with_abstract
# ---------------------------------------------------------------------------


class TestEfetchPapersWithAbstract:
    def test_uses_injected_fetcher(self, tmp_path: Path) -> None:
        def fake(batch: list[str]) -> bytes:
            return _RELEVANCE_XML

        out = efetch_papers_with_abstract(["11111"], cache_dir=tmp_path, fetcher=fake)
        assert "11111" in out
        assert out["11111"].abstract != ""

    def test_caches_per_pmid(self, tmp_path: Path) -> None:
        calls: list[list[str]] = []

        def fake(batch: list[str]) -> bytes:
            calls.append(batch)
            return _RELEVANCE_XML

        first = efetch_papers_with_abstract(["11111"], cache_dir=tmp_path, fetcher=fake)
        second = efetch_papers_with_abstract(
            ["11111"], cache_dir=tmp_path, fetcher=fake
        )
        assert first.keys() == second.keys()
        assert len(calls) == 1

    def test_cached_record_preserves_abstract(self, tmp_path: Path) -> None:
        def fake(batch: list[str]) -> bytes:
            return _RELEVANCE_XML

        first = efetch_papers_with_abstract(["11111"], cache_dir=tmp_path, fetcher=fake)

        # Force re-read of cache by calling again with a fetcher that
        # would crash if invoked.
        def angry(batch: list[str]) -> bytes:
            raise AssertionError("should not be called — cache hit expected")

        second = efetch_papers_with_abstract(
            ["11111"], cache_dir=tmp_path, fetcher=angry
        )
        assert first["11111"].abstract == second["11111"].abstract

    def test_empty_input_returns_empty(self, tmp_path: Path) -> None:
        assert efetch_papers_with_abstract([], cache_dir=tmp_path) == {}

    def test_missing_pmid_in_response_is_omitted(self, tmp_path: Path) -> None:
        def fake(batch: list[str]) -> bytes:
            return _RELEVANCE_XML  # has 11111, 22222, 33333, 44444

        out = efetch_papers_with_abstract(
            ["11111", "99999"], cache_dir=tmp_path, fetcher=fake
        )
        assert "11111" in out
        assert "99999" not in out


# ---------------------------------------------------------------------------
# score_relevance — MeSH-first, LLM-fallback strategy
# ---------------------------------------------------------------------------


def _record(pmid: str, *, mesh: list[str], abstract: str = "") -> PaperRecord:
    return PaperRecord(
        pmid=pmid,
        title="title",
        abstract=abstract,
        journal="J",
        year="2024",
        mesh=mesh,
    )


class TestScoreRelevance:
    def test_mesh_hit_marks_relevant(self) -> None:
        score = score_relevance(
            _record("1", mesh=["Stroke", "Humans"]),
            relevant_mesh_set={"Stroke"},
        )
        assert score.relevant is True
        assert score.source == "mesh"
        assert score.confidence == 1.0
        assert score.matched_mesh == "Stroke"

    def test_mesh_present_no_hit_marks_not_relevant(self) -> None:
        score = score_relevance(
            _record("2", mesh=["Hypertension"]),
            relevant_mesh_set={"Stroke"},
        )
        assert score.relevant is False
        assert score.source == "mesh"
        assert score.matched_mesh is None

    def test_no_mesh_calls_llm_scorer(self) -> None:
        called_pmids: list[str] = []

        def fake_llm(rec: PaperRecord) -> LlmVerdict | None:
            called_pmids.append(rec.pmid)
            return LlmVerdict(relevant=True, confidence=0.9, reason="cSVD focus")

        score = score_relevance(
            _record("3", mesh=[], abstract="about cSVD"),
            relevant_mesh_set={"Stroke"},
            llm_scorer=fake_llm,
        )
        assert called_pmids == ["3"]
        assert score.source == "llm"
        assert score.relevant is True
        assert score.confidence == 0.9
        assert score.reason == "cSVD focus"

    def test_no_mesh_no_llm_fallback_unscoreable(self) -> None:
        score = score_relevance(
            _record("4", mesh=[]),
            relevant_mesh_set={"Stroke"},
            llm_scorer=None,
        )
        assert score.relevant is False
        assert score.source == "unscoreable"

    def test_llm_returning_none_yields_unscoreable(self) -> None:
        def disabled_llm(_rec: PaperRecord) -> LlmVerdict | None:
            return None

        score = score_relevance(
            _record("5", mesh=[]),
            relevant_mesh_set={"Stroke"},
            llm_scorer=disabled_llm,
        )
        assert score.source == "unscoreable"
        assert "no API key" in score.reason or "call failed" in score.reason

    def test_first_mesh_hit_wins(self) -> None:
        score = score_relevance(
            _record("6", mesh=["Stroke", "CADASIL"]),
            relevant_mesh_set={"Stroke", "CADASIL"},
        )
        assert score.matched_mesh == "Stroke"


# ---------------------------------------------------------------------------
# compute_recall_floor
# ---------------------------------------------------------------------------


class TestComputeRecallFloor:
    def test_full_recall(self) -> None:
        rf = compute_recall_floor(["1", "2", "3"], ["1", "2"])
        assert rf.retrieved == 2
        assert rf.total_gold == 2
        assert rf.recall == 1.0
        assert rf.missing == []

    def test_partial_recall(self) -> None:
        rf = compute_recall_floor(["1"], ["1", "2", "3"])
        assert rf.retrieved == 1
        assert rf.total_gold == 3
        assert rf.recall == pytest.approx(1 / 3)
        assert rf.missing == ["2", "3"]

    def test_empty_gold_returns_zero_total(self) -> None:
        rf = compute_recall_floor(["1", "2"], [])
        assert rf.total_gold == 0
        assert rf.recall == 0.0
        assert rf.missing == []

    def test_duplicates_collapsed(self) -> None:
        rf = compute_recall_floor(["1", "1", "2"], ["1", "1"])
        assert rf.retrieved == 1
        assert rf.total_gold == 1


# ---------------------------------------------------------------------------
# load_bibliography_gold_pmids
# ---------------------------------------------------------------------------


class TestLoadBibliographyGoldPmids:
    def test_extracts_pmid_stems(self, tmp_path: Path) -> None:
        (tmp_path / "27664989.xml").write_text("x", encoding="utf-8")
        (tmp_path / "15905468.xml").write_text("x", encoding="utf-8")
        pmids = load_bibliography_gold_pmids(tmp_path)
        assert pmids == ["15905468", "27664989"]

    def test_non_numeric_stems_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "27664989.xml").write_text("x", encoding="utf-8")
        (tmp_path / "notes.xml").write_text("x", encoding="utf-8")
        pmids = load_bibliography_gold_pmids(tmp_path)
        assert pmids == ["27664989"]

    def test_missing_dir_returns_empty(self, tmp_path: Path) -> None:
        assert load_bibliography_gold_pmids(tmp_path / "nope") == []


# ---------------------------------------------------------------------------
# sample_pmids
# ---------------------------------------------------------------------------


class TestSamplePmids:
    def test_returns_all_when_fewer_than_sample_size(self) -> None:
        out = sample_pmids(["3", "1", "2"], sample_size=10, seed=0)
        assert out == ["1", "2", "3"]

    def test_deterministic_with_same_seed(self) -> None:
        pool = [str(i) for i in range(100)]
        a = sample_pmids(pool, sample_size=10, seed=42)
        b = sample_pmids(pool, sample_size=10, seed=42)
        assert a == b
        assert len(a) == 10

    def test_different_seeds_can_differ(self) -> None:
        pool = [str(i) for i in range(100)]
        a = sample_pmids(pool, sample_size=10, seed=1)
        b = sample_pmids(pool, sample_size=10, seed=2)
        assert a != b

    def test_empty_pool_returns_empty(self) -> None:
        assert sample_pmids([], sample_size=10, seed=0) == []

    def test_results_sorted(self) -> None:
        pool = [str(i) for i in range(100)]
        out = sample_pmids(pool, sample_size=5, seed=42)
        assert out == sorted(out)


# ---------------------------------------------------------------------------
# _parse_llm_response — Anthropic content-block extraction
# ---------------------------------------------------------------------------


class _MockTextBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _MockResponse:
    def __init__(self, blocks: list[_MockTextBlock]) -> None:
        self.content = blocks


class TestParseLlmResponse:
    def test_plain_json(self) -> None:
        resp = _MockResponse(
            [_MockTextBlock('{"relevant": true, "confidence": 0.9, "reason": "x"}')]
        )
        v = _parse_llm_response(resp)
        assert v is not None
        assert v.relevant is True
        assert v.confidence == 0.9

    def test_fenced_json(self) -> None:
        fenced = '```json\n{"relevant": false, "confidence": 0.3, "reason": "y"}\n```'
        resp = _MockResponse([_MockTextBlock(fenced)])
        v = _parse_llm_response(resp)
        assert v is not None
        assert v.relevant is False

    def test_confidence_clamped(self) -> None:
        resp = _MockResponse(
            [_MockTextBlock('{"relevant": true, "confidence": 1.5, "reason": "x"}')]
        )
        v = _parse_llm_response(resp)
        assert v is not None
        assert v.confidence == 1.0

    def test_invalid_json_returns_none(self) -> None:
        resp = _MockResponse([_MockTextBlock("not json")])
        assert _parse_llm_response(resp) is None

    def test_missing_required_field_returns_none(self) -> None:
        resp = _MockResponse([_MockTextBlock('{"confidence": 0.5}')])
        assert _parse_llm_response(resp) is None

    def test_empty_content_returns_none(self) -> None:
        resp = _MockResponse([])
        assert _parse_llm_response(resp) is None


# ---------------------------------------------------------------------------
# LlmRelevanceScorer — caching + degradation
# ---------------------------------------------------------------------------


class TestLlmRelevanceScorer:
    def test_no_api_key_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        scorer = LlmRelevanceScorer(cache_dir=tmp_path)
        rec = _record("1", mesh=[], abstract="something")
        assert scorer.score(rec) is None

    def test_reads_cache(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        scorer = LlmRelevanceScorer(cache_dir=tmp_path)
        cache_path = scorer.cache_path("9")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(
                {
                    "pmid": "9",
                    "model": scorer.model,
                    "relevant": True,
                    "confidence": 0.77,
                    "reason": "cached verdict",
                }
            ),
            encoding="utf-8",
        )
        rec = _record("9", mesh=[], abstract="text")
        v = scorer.score(rec)
        assert v is not None
        assert v.relevant is True
        assert v.confidence == 0.77

    def test_corrupt_cache_falls_through_to_disabled(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        scorer = LlmRelevanceScorer(cache_dir=tmp_path)
        cache_path = scorer.cache_path("10")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text("garbage", encoding="utf-8")
        rec = _record("10", mesh=[], abstract="text")
        with caplog.at_level("WARNING"):
            assert scorer.score(rec) is None
        # Either the corrupt-cache warning or the no-API-key warning is fine.
        assert any(
            "corrupt" in r.message.lower() or "api" in r.message.lower()
            for r in caplog.records
        )


# ---------------------------------------------------------------------------
# render_validate_report
# ---------------------------------------------------------------------------


def _qv(
    *,
    label: str,
    pmids: list[str],
    scores: list[RelevanceScore],
    recall: RecallFloor,
    total: int | None = None,
) -> QueryValidation:
    return QueryValidation(
        label=label,
        query=f'"{label}"[Title/Abstract]',
        total_pmids=total if total is not None else len(pmids),
        truncated=False,
        sample_pmids=pmids,
        scores=scores,
        recall_floor=recall,
    )


def _report() -> ValidationReport:
    distilled = _qv(
        label="distilled",
        pmids=["1", "2", "3"],
        scores=[
            RelevanceScore("1", True, "mesh", 1.0, "MeSH match: Stroke", "Stroke"),
            RelevanceScore("2", False, "mesh", 1.0, "indexed but no cSVD MeSH"),
            RelevanceScore("3", True, "llm", 0.8, "abstract focuses on cSVD"),
        ],
        recall=RecallFloor(retrieved=2, total_gold=3, missing=["999"]),
    )
    production = _qv(
        label="SVD_QUERY",
        pmids=["1", "4"],
        scores=[
            RelevanceScore("1", True, "mesh", 1.0, "MeSH match: Stroke", "Stroke"),
            RelevanceScore("4", True, "mesh", 1.0, "MeSH match: CADASIL", "CADASIL"),
        ],
        recall=RecallFloor(retrieved=3, total_gold=3, missing=[]),
    )
    return ValidationReport(
        distilled=distilled,
        production=production,
        relevant_mesh_set=["CADASIL", "Stroke"],
        gold_pmids=["1", "2", "999"],
        sample_size=200,
        seed=0,
        validate_since="2023/01/01",
        validate_until=None,
        llm_model="claude-haiku-4-5-20251001",
    )


class TestRenderValidateReport:
    def test_all_sections_present(self) -> None:
        out = render_validate_report(_report())
        assert "PubMed query relevance validation" in out
        assert "## Queries" in out
        assert "## Precision" in out
        assert "## Recall floor" in out
        assert "## Score sources" in out
        assert "## Retrieved totals" in out
        assert "## Relevant-MeSH set" in out
        assert "## Sample papers — distilled" in out
        assert "## Sample papers — SVD_QUERY" in out
        assert "## Interpretation" in out

    def test_precision_rendered(self) -> None:
        out = render_validate_report(_report())
        # distilled: 2 relevant / 3 scoreable → 66.7%
        assert "66.7%" in out
        # SVD_QUERY: 2 relevant / 2 scoreable → 100.0%
        assert "100.0%" in out

    def test_missing_pmids_shown(self) -> None:
        out = render_validate_report(_report())
        assert "999" in out  # missing from distilled

    def test_relevant_mesh_set_listed(self) -> None:
        out = render_validate_report(_report())
        assert "- CADASIL" in out
        assert "- Stroke" in out

    def test_interpretation_notes_recall_miss(self) -> None:
        out = render_validate_report(_report())
        assert "misses" in out


# ---------------------------------------------------------------------------
# QueryValidation derived properties
# ---------------------------------------------------------------------------


class TestQueryValidationProperties:
    def test_precision_with_unscoreable_excluded(self) -> None:
        qv = _qv(
            label="X",
            pmids=["1", "2", "3"],
            scores=[
                RelevanceScore("1", True, "mesh", 1.0, "r"),
                RelevanceScore("2", False, "mesh", 1.0, "r"),
                RelevanceScore("3", False, "unscoreable", 0.0, "r"),
            ],
            recall=RecallFloor(0, 0, []),
        )
        # Only 2 scoreable; 1 relevant → 0.5
        assert qv.precision == 0.5
        assert qv.scoreable_sample == 2
        assert qv.relevant_count == 1

    def test_precision_none_when_all_unscoreable(self) -> None:
        qv = _qv(
            label="X",
            pmids=["1"],
            scores=[RelevanceScore("1", False, "unscoreable", 0.0, "r")],
            recall=RecallFloor(0, 0, []),
        )
        assert qv.precision is None

    def test_source_counts(self) -> None:
        qv = _qv(
            label="X",
            pmids=["1", "2", "3"],
            scores=[
                RelevanceScore("1", True, "mesh", 1.0, "r"),
                RelevanceScore("2", True, "llm", 0.8, "r"),
                RelevanceScore("3", False, "unscoreable", 0.0, "r"),
            ],
            recall=RecallFloor(0, 0, []),
        )
        c = qv.source_counts()
        assert c == {"mesh": 1, "llm": 1, "unscoreable": 1}


# ---------------------------------------------------------------------------
# emit_validate_json — JSON sidecar round-trip
# ---------------------------------------------------------------------------


class TestEmitValidateJson:
    def test_round_trips_key_fields(self, tmp_path: Path) -> None:
        report = _report()
        out_path = tmp_path / "out.json"
        emit_validate_json(report, out_path)
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        assert payload["sample_size"] == 200
        assert payload["seed"] == 0
        assert payload["llm_model"] == "claude-haiku-4-5-20251001"
        assert payload["relevant_mesh_set"] == ["CADASIL", "Stroke"]
        assert payload["queries"]["distilled"]["precision"] == pytest.approx(2 / 3)
        assert payload["queries"]["production"]["precision"] == 1.0
        # Scores serialized
        assert len(payload["queries"]["distilled"]["scores"]) == 3
        assert payload["queries"]["distilled"]["scores"][0]["pmid"] == "1"
        assert payload["queries"]["distilled"]["scores"][0]["matched_mesh"] == "Stroke"


# ---------------------------------------------------------------------------
# run_validate — end-to-end with injected fetchers + scorer
# ---------------------------------------------------------------------------


class TestRunValidate:
    def _setup_mesh_dir(self, tmp_path: Path) -> Path:
        mesh_dir = tmp_path / "mesh"
        mesh_dir.mkdir()
        for i in range(3):
            _write_mesh_json(mesh_dir / f"{i}.json", str(i), ["Stroke"])
        return mesh_dir

    def _setup_xml_dir(self, tmp_path: Path) -> Path:
        xml_dir = tmp_path / "xml"
        xml_dir.mkdir()
        (xml_dir / "11111.xml").write_text("x", encoding="utf-8")
        return xml_dir

    def test_end_to_end_with_mocks(self, tmp_path: Path) -> None:
        mesh_dir = self._setup_mesh_dir(tmp_path)
        xml_dir = self._setup_xml_dir(tmp_path)
        cache_dir = tmp_path / "cache"

        def fake_esearch(**kwargs: Any) -> dict[str, Any]:
            term = str(kwargs.get("term", ""))
            if "distilled" in term:
                return {"IdList": ["11111", "22222"], "Count": "2"}
            return {"IdList": ["11111", "33333"], "Count": "2"}

        def fake_efetch(batch: list[str]) -> bytes:
            return _RELEVANCE_XML

        # Inject a deterministic LLM scorer so PMID 33333 (no MeSH)
        # gets a verdict without touching the Anthropic SDK.
        def fake_llm(record: PaperRecord) -> LlmVerdict | None:
            return LlmVerdict(
                relevant="cSVD" in record.abstract or "lacunar" in record.abstract,
                confidence=0.9,
                reason="mock verdict",
            )

        report = run_validate(
            distilled_query="distilled query",
            production_query="production query",
            sample_size=10,
            mesh_threshold=3,
            mesh_dir=mesh_dir,
            bibliography_xml_dir=xml_dir,
            cache_dir=cache_dir,
            esearch_fetcher=fake_esearch,
            efetch_fetcher=fake_efetch,
            llm_scorer=fake_llm,
        )

        # Both queries got scored.
        assert len(report.distilled.scores) == 2
        assert len(report.production.scores) == 2

        # The Stroke-tagged sample papers (11111 has CADASIL via MeSH;
        # 22222 has Hypertension only — no relevant MeSH) should be
        # scored via the mesh path.
        distilled_sources = {s.source for s in report.distilled.scores}
        assert "mesh" in distilled_sources

        # PMID 33333 has no MeSH → LLM fallback.
        production_sources = [(s.pmid, s.source) for s in report.production.scores]
        assert ("33333", "llm") in production_sources

        # Recall floor uses the no-date esearch result; bibliography
        # gold has just 11111.
        assert report.distilled.recall_floor.total_gold == 1
        assert report.production.recall_floor.total_gold == 1
        # Both queries retrieve PMID 11111, so recall is 1.0
        assert report.distilled.recall_floor.recall == 1.0
        assert report.production.recall_floor.recall == 1.0

        # Relevant-MeSH set includes the empirical "Stroke" + the floor.
        assert "Stroke" in report.relevant_mesh_set
        assert "CADASIL" in report.relevant_mesh_set

    def test_render_emits_markdown(self, tmp_path: Path) -> None:
        mesh_dir = self._setup_mesh_dir(tmp_path)
        xml_dir = self._setup_xml_dir(tmp_path)

        def fake_esearch(**_kwargs: Any) -> dict[str, Any]:
            return {"IdList": ["11111"], "Count": "1"}

        def fake_efetch(batch: list[str]) -> bytes:
            return _RELEVANCE_XML

        report = run_validate(
            distilled_query="dq",
            production_query="pq",
            sample_size=5,
            mesh_threshold=3,
            mesh_dir=mesh_dir,
            bibliography_xml_dir=xml_dir,
            cache_dir=tmp_path,
            esearch_fetcher=fake_esearch,
            efetch_fetcher=fake_efetch,
            llm_scorer=lambda _r: LlmVerdict(False, 0.0, "n/a"),
        )
        markdown = render_validate_report(report)
        assert "PubMed query relevance validation" in markdown
        assert "## Precision" in markdown


# ---------------------------------------------------------------------------
# Floor constants — sanity check that the canonical headings are present
# ---------------------------------------------------------------------------


def test_default_mesh_floor_contains_canonical_terms() -> None:
    assert "Cerebral Small Vessel Diseases" in _RELEVANT_MESH_FLOOR
    assert "CADASIL" in _RELEVANT_MESH_FLOOR
    assert "Leukoaraiosis" in _RELEVANT_MESH_FLOOR
