"""Tests for pipeline.clinical_trials_fetch — CTG v2 discovery + refresh."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from pipeline.clinical_trials_fetch import (
    ClinicalTrialRecord,
    _drug_interventions,
    _first_phase,
    _first_primary_outcome,
    _get_path,
    _map_study_to_records,
    fetch_csvd_studies,
    sync_clinical_trials,
)
from pipeline.config import PipelineConfig

# ---------------------------------------------------------------------------
# Study dict builders (inline fixtures)
# ---------------------------------------------------------------------------


def _make_study(
    nct_id: str = "NCT00000001",
    brief_title: str | None = "A cSVD trial",
    phases: list[str] | None = None,
    interventions: list[dict] | None = None,
    enrollment_count: int | None = 100,
    completion_date: str | None = "2026-12-31",
    primary_outcomes: list[dict] | None = None,
    sponsor_class: str | None = "INDUSTRY",
) -> dict:
    """Build a minimal CTG v2 study dict matching the protocolSection shape."""
    study: dict = {
        "protocolSection": {
            "identificationModule": {"nctId": nct_id},
            "designModule": {},
            "armsInterventionsModule": {},
            "statusModule": {},
            "outcomesModule": {},
            "sponsorCollaboratorsModule": {},
        }
    }
    if brief_title is not None:
        study["protocolSection"]["identificationModule"]["briefTitle"] = brief_title
    if phases is not None:
        study["protocolSection"]["designModule"]["phases"] = phases
    if enrollment_count is not None:
        study["protocolSection"]["designModule"]["enrollmentInfo"] = {
            "count": enrollment_count
        }
    if interventions is not None:
        study["protocolSection"]["armsInterventionsModule"]["interventions"] = (
            interventions
        )
    if completion_date is not None:
        study["protocolSection"]["statusModule"]["completionDateStruct"] = {
            "date": completion_date
        }
    if primary_outcomes is not None:
        study["protocolSection"]["outcomesModule"]["primaryOutcomes"] = primary_outcomes
    if sponsor_class is not None:
        study["protocolSection"]["sponsorCollaboratorsModule"]["leadSponsor"] = {
            "class": sponsor_class
        }
    return study


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestGetPath:
    def test_traverses_nested_dicts(self):
        data = {"a": {"b": {"c": "value"}}}
        assert _get_path(data, "a", "b", "c") == "value"

    def test_returns_none_on_missing_key(self):
        data = {"a": {"b": 1}}
        assert _get_path(data, "a", "missing") is None

    def test_returns_none_on_non_dict(self):
        data = {"a": [1, 2, 3]}
        assert _get_path(data, "a", "b") is None


class TestDrugInterventions:
    def test_extracts_drug_only(self):
        study = _make_study(
            interventions=[
                {"type": "DRUG", "name": "aspirin"},
                {"type": "BEHAVIORAL", "name": "education"},
                {"type": "DEVICE", "name": "pump"},
                {"type": "DRUG", "name": "clopidogrel"},
            ]
        )
        assert _drug_interventions(study) == ["aspirin", "clopidogrel"]

    def test_deduplicates_by_name(self):
        study = _make_study(
            interventions=[
                {"type": "DRUG", "name": "aspirin"},
                {"type": "DRUG", "name": "aspirin"},
            ]
        )
        assert _drug_interventions(study) == ["aspirin"]

    def test_empty_when_no_drugs(self):
        study = _make_study(interventions=[{"type": "BEHAVIORAL", "name": "education"}])
        assert _drug_interventions(study) == []

    def test_skips_missing_name_or_type(self):
        study = _make_study(
            interventions=[
                {"type": "DRUG"},  # no name
                {"name": "aspirin"},  # no type
                {"type": "DRUG", "name": ""},  # empty name
                {"type": "DRUG", "name": "valid"},
            ]
        )
        assert _drug_interventions(study) == ["valid"]

    def test_case_insensitive_type_match(self):
        study = _make_study(interventions=[{"type": "drug", "name": "aspirin"}])
        assert _drug_interventions(study) == ["aspirin"]

    def test_deduplicates_whitespace_variants(self):
        # Whitespace-padded duplicates used to bypass the dedup check and
        # produce two records that violated the UNIQUE(registry_id, drug)
        # constraint on upsert.
        study = _make_study(
            interventions=[
                {"type": "DRUG", "name": "aspirin"},
                {"type": "DRUG", "name": " aspirin "},
                {"type": "DRUG", "name": "aspirin\t"},
            ]
        )
        assert _drug_interventions(study) == ["aspirin"]


class TestFirstPhase:
    def test_returns_first_phase_mapped_to_display_label(self):
        study = _make_study(phases=["PHASE2", "PHASE3"])
        assert _first_phase(study) == "Phase 2"

    def test_maps_early_phase_1(self):
        study = _make_study(phases=["EARLY_PHASE1"])
        assert _first_phase(study) == "Early Phase 1"

    def test_unmapped_phase_passes_through(self):
        study = _make_study(phases=["UNKNOWN_PHASE"])
        assert _first_phase(study) == "UNKNOWN_PHASE"

    def test_none_when_missing(self):
        study = _make_study(phases=None)
        assert _first_phase(study) is None

    def test_none_when_empty(self):
        study = _make_study(phases=[])
        assert _first_phase(study) is None


class TestFirstPrimaryOutcome:
    def test_returns_first_measure(self):
        study = _make_study(
            primary_outcomes=[
                {"measure": "Change in WMH volume"},
                {"measure": "Second outcome"},
            ]
        )
        assert _first_primary_outcome(study) == "Change in WMH volume"

    def test_none_when_missing(self):
        study = _make_study(primary_outcomes=None)
        assert _first_primary_outcome(study) is None

    def test_none_when_no_measure_field(self):
        study = _make_study(primary_outcomes=[{"timeFrame": "6 months"}])
        assert _first_primary_outcome(study) is None


# ---------------------------------------------------------------------------
# _map_study_to_records
# ---------------------------------------------------------------------------


class TestMapStudyToRecords:
    def test_single_drug_emits_one_record(self):
        study = _make_study(
            nct_id="NCT12345678",
            brief_title="Aspirin for lacunar stroke",
            phases=["PHASE3"],
            interventions=[{"type": "DRUG", "name": "aspirin"}],
            enrollment_count=500,
            completion_date="2027-06-30",
            primary_outcomes=[{"measure": "Recurrent stroke rate"}],
            sponsor_class="NIH",
        )
        records = _map_study_to_records(study)

        assert len(records) == 1
        r = records[0]
        assert r.registry_id == "NCT12345678"
        assert r.drug == "aspirin"
        assert r.trial_name == "Aspirin for lacunar stroke"
        assert r.clinical_trial_phase == "Phase 3"
        assert r.target_sample_size == 500
        assert r.estimated_completion_date == "2027-06-30"
        assert r.primary_outcome == "Recurrent stroke rate"
        assert r.sponsor_type == "NIH"

    def test_multi_drug_emits_one_record_per_drug(self):
        study = _make_study(
            nct_id="NCT99999999",
            interventions=[
                {"type": "DRUG", "name": "aspirin"},
                {"type": "DRUG", "name": "clopidogrel"},
                {"type": "BEHAVIORAL", "name": "education"},
            ],
        )
        records = _map_study_to_records(study)
        assert len(records) == 2
        drugs = sorted(r.drug for r in records)
        assert drugs == ["aspirin", "clopidogrel"]
        assert all(r.registry_id == "NCT99999999" for r in records)

    def test_non_drug_interventions_yield_empty(self):
        study = _make_study(
            interventions=[
                {"type": "BEHAVIORAL", "name": "education"},
                {"type": "DEVICE", "name": "pump"},
            ]
        )
        assert _map_study_to_records(study) == []

    def test_missing_optional_fields_resolve_to_none(self):
        study = _make_study(
            nct_id="NCT00000002",
            brief_title=None,
            phases=None,
            interventions=[{"type": "DRUG", "name": "drug1"}],
            enrollment_count=None,
            completion_date=None,
            primary_outcomes=None,
            sponsor_class=None,
        )
        records = _map_study_to_records(study)
        assert len(records) == 1
        r = records[0]
        assert r.registry_id == "NCT00000002"
        assert r.drug == "drug1"
        assert r.trial_name is None
        assert r.clinical_trial_phase is None
        assert r.target_sample_size is None
        assert r.estimated_completion_date is None
        assert r.primary_outcome is None
        assert r.sponsor_type is None

    def test_missing_nct_id_yields_empty(self):
        study = {"protocolSection": {"identificationModule": {}}}
        assert _map_study_to_records(study) == []

    def test_no_interventions_yields_empty(self):
        study = _make_study(interventions=None)
        assert _map_study_to_records(study) == []

    def test_non_int_enrollment_becomes_none(self):
        # CTG is mostly consistent but a stringified count has been observed —
        # we must not crash and must resolve to None.
        study = _make_study(
            interventions=[{"type": "DRUG", "name": "d"}],
            enrollment_count=None,
        )
        study["protocolSection"]["designModule"]["enrollmentInfo"] = {"count": "500"}
        records = _map_study_to_records(study)
        assert records[0].target_sample_size is None


# ---------------------------------------------------------------------------
# fetch_csvd_studies (pagination + dedup)
# ---------------------------------------------------------------------------


def _mock_http_response(json_body: dict, status_code: int = 200) -> AsyncMock:
    """Build an AsyncMock that mimics an httpx.Response."""
    resp = AsyncMock()
    resp.status_code = status_code
    resp.json = lambda: json_body  # sync method on httpx.Response
    resp.raise_for_status = AsyncMock()
    return resp


class TestFetchCSVDStudies:
    async def test_single_page_single_term(self, mocker):
        body = {
            "studies": [
                _make_study(
                    nct_id="NCT11111111",
                    interventions=[{"type": "DRUG", "name": "aspirin"}],
                )
            ],
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_http_response(body))
        mocker.patch(
            "pipeline.clinical_trials_fetch._client_manager.get",
            return_value=mock_client,
        )

        studies, errors = await fetch_csvd_studies(
            search_terms=("lacunar stroke",),
            page_size=100,
            max_retries=0,
        )
        assert len(studies) == 1
        assert errors == []
        # One HTTP GET per term when there's no pagination
        assert mock_client.get.call_count == 1

    async def test_pagination_follows_next_page_token(self, mocker):
        page1 = {
            "studies": [_make_study(nct_id="NCT1")],
            "nextPageToken": "tok-2",
        }
        page2 = {
            "studies": [_make_study(nct_id="NCT2")],
            # no nextPageToken -> loop terminates
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=[
                _mock_http_response(page1),
                _mock_http_response(page2),
            ]
        )
        mocker.patch(
            "pipeline.clinical_trials_fetch._client_manager.get",
            return_value=mock_client,
        )

        studies, errors = await fetch_csvd_studies(
            search_terms=("lacunar stroke",),
            page_size=100,
            max_retries=0,
        )
        nct_ids = sorted(
            _get_path(s, "protocolSection", "identificationModule", "nctId")
            for s in studies
        )
        assert nct_ids == ["NCT1", "NCT2"]
        assert errors == []

    async def test_dedup_across_terms(self, mocker):
        # Term1 returns NCT1 + NCT2; Term2 returns NCT2 + NCT3.
        # Expect NCT1, NCT2, NCT3 (NCT2 deduped).
        term1_body = {
            "studies": [_make_study(nct_id="NCT1"), _make_study(nct_id="NCT2")],
        }
        term2_body = {
            "studies": [_make_study(nct_id="NCT2"), _make_study(nct_id="NCT3")],
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=[
                _mock_http_response(term1_body),
                _mock_http_response(term2_body),
            ]
        )
        mocker.patch(
            "pipeline.clinical_trials_fetch._client_manager.get",
            return_value=mock_client,
        )

        studies, errors = await fetch_csvd_studies(
            search_terms=("term1", "term2"),
            page_size=100,
            max_retries=0,
        )
        nct_ids = sorted(
            _get_path(s, "protocolSection", "identificationModule", "nctId")
            for s in studies
        )
        assert nct_ids == ["NCT1", "NCT2", "NCT3"]
        assert errors == []

    async def test_empty_terms_returns_empty(self):
        studies, errors = await fetch_csvd_studies(
            search_terms=(),
            page_size=100,
            max_retries=0,
        )
        assert studies == []
        assert errors == []

    async def test_retry_on_5xx(self, mocker):
        good = {"studies": [_make_study(nct_id="NCT1")]}
        mock_client = AsyncMock()
        fail_resp = AsyncMock()
        fail_resp.status_code = 503
        fail_resp.request = httpx.Request("GET", "https://x/y")
        fail_resp.json = lambda: {}
        fail_resp.raise_for_status = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[fail_resp, _mock_http_response(good)])
        mocker.patch(
            "pipeline.clinical_trials_fetch._client_manager.get",
            return_value=mock_client,
        )
        mocker.patch("pipeline.clinical_trials_fetch.asyncio.sleep", new=AsyncMock())

        studies, errors = await fetch_csvd_studies(
            search_terms=("term",),
            page_size=100,
            max_retries=2,
        )
        assert len(studies) == 1
        assert errors == []

    async def test_partial_term_failure_preserves_others(self, mocker):
        # One term returns studies, the other raises after retry exhaustion;
        # with gather, the successful term's results must survive.
        from pipeline import clinical_trials_fetch as ctg

        good = [_make_study(nct_id="NCT_GOOD")]

        async def fake_search(term, page_size, max_retries):
            if term == "bad":
                raise RuntimeError("term bad blew up")
            return good

        mocker.patch.object(ctg, "_search_condition_term", side_effect=fake_search)

        studies, errors = await fetch_csvd_studies(
            search_terms=("good", "bad"),
            page_size=100,
            max_retries=0,
        )
        assert len(studies) == 1
        assert (
            _get_path(studies[0], "protocolSection", "identificationModule", "nctId")
            == "NCT_GOOD"
        )
        assert len(errors) == 1
        assert "bad" in errors[0]

    async def test_page_level_failure_preserves_earlier_pages(self, mocker):
        # Page 1 succeeds and has a nextPageToken; page 2 fails all retries.
        # Expect page 1's studies preserved rather than discarded.
        page1 = {
            "studies": [_make_study(nct_id="NCT_P1")],
            "nextPageToken": "tok-2",
        }

        call_count = {"n": 0}

        def get_side_effect(*_args, **_kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return _mock_http_response(page1)
            fail = AsyncMock()
            fail.status_code = 503
            fail.request = httpx.Request("GET", "https://x/y")
            fail.json = lambda: {}
            fail.raise_for_status = AsyncMock()
            return fail

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=get_side_effect)
        mocker.patch(
            "pipeline.clinical_trials_fetch._client_manager.get",
            return_value=mock_client,
        )
        mocker.patch("pipeline.clinical_trials_fetch.asyncio.sleep", new=AsyncMock())

        studies, errors = await fetch_csvd_studies(
            search_terms=("term",),
            page_size=100,
            max_retries=1,
        )
        assert len(studies) == 1
        # Term itself is "successful" (returns partial results), so no term-level
        # error — the page-level warning is logged but not surfaced.
        assert errors == []


# ---------------------------------------------------------------------------
# sync_clinical_trials (end-to-end with mocked DB)
# ---------------------------------------------------------------------------


class TestSyncClinicalTrials:
    async def test_happy_path(self, mocker):
        studies = [
            _make_study(
                nct_id="NCT1",
                interventions=[{"type": "DRUG", "name": "aspirin"}],
            ),
            _make_study(
                nct_id="NCT2",
                interventions=[
                    {"type": "DRUG", "name": "drug-a"},
                    {"type": "DRUG", "name": "drug-b"},
                ],
            ),
        ]
        mocker.patch(
            "pipeline.clinical_trials_fetch.fetch_csvd_studies",
            return_value=(studies, []),
        )
        mock_upsert = mocker.patch(
            "pipeline.database.upsert_clinical_trials_batch",
            new_callable=AsyncMock,
            return_value=3,
        )

        config = PipelineConfig()
        result = await sync_clinical_trials(config)

        # 2 studies hit, 3 records (1 from NCT1 + 2 from NCT2) upserted
        assert result.fetched == 2
        assert result.cached == 3
        assert result.failed == 0
        assert result.errors == []

        # Upsert was called with exactly 3 records
        args, _kwargs = mock_upsert.call_args
        passed_records = args[0]
        assert len(passed_records) == 3
        assert all(isinstance(r, ClinicalTrialRecord) for r in passed_records)

    async def test_fetch_failure_returns_error(self, mocker):
        mocker.patch(
            "pipeline.clinical_trials_fetch.fetch_csvd_studies",
            side_effect=RuntimeError("boom"),
        )
        config = PipelineConfig()
        result = await sync_clinical_trials(config)

        assert result.fetched == 0
        assert result.cached == 0
        assert "boom" in result.errors[0]

    async def test_upsert_failure_returns_error(self, mocker):
        studies = [
            _make_study(
                nct_id="NCT1",
                interventions=[{"type": "DRUG", "name": "drug"}],
            )
        ]
        mocker.patch(
            "pipeline.clinical_trials_fetch.fetch_csvd_studies",
            return_value=(studies, []),
        )
        mocker.patch(
            "pipeline.database.upsert_clinical_trials_batch",
            new_callable=AsyncMock,
            side_effect=RuntimeError("db down"),
        )

        config = PipelineConfig()
        result = await sync_clinical_trials(config)

        assert result.fetched == 1
        assert result.cached == 0
        assert result.failed == 1
        assert any("db down" in e for e in result.errors)

    async def test_studies_without_drugs_are_skipped(self, mocker):
        studies = [
            _make_study(nct_id="NCT1", interventions=[{"type": "DRUG", "name": "d"}]),
            _make_study(
                nct_id="NCT2",
                interventions=[{"type": "BEHAVIORAL", "name": "edu"}],
            ),
        ]
        mocker.patch(
            "pipeline.clinical_trials_fetch.fetch_csvd_studies",
            return_value=(studies, []),
        )
        mock_upsert = mocker.patch(
            "pipeline.database.upsert_clinical_trials_batch",
            new_callable=AsyncMock,
            return_value=1,
        )
        config = PipelineConfig()
        result = await sync_clinical_trials(config)

        # Both studies are reported as "fetched", but only NCT1 becomes a record
        assert result.fetched == 2
        args, _ = mock_upsert.call_args
        assert len(args[0]) == 1
        assert args[0][0].registry_id == "NCT1"

    async def test_term_failures_surfaced_in_errors(self, mocker):
        # fetch_csvd_studies returns (studies, term_errors); the term errors
        # must propagate into SyncResult.errors.
        studies = [
            _make_study(
                nct_id="NCT_OK",
                interventions=[{"type": "DRUG", "name": "drug"}],
            )
        ]
        term_errors = ["CTG term 'bad': boom"]
        mocker.patch(
            "pipeline.clinical_trials_fetch.fetch_csvd_studies",
            return_value=(studies, term_errors),
        )
        mocker.patch(
            "pipeline.database.upsert_clinical_trials_batch",
            new_callable=AsyncMock,
            return_value=1,
        )
        config = PipelineConfig()
        result = await sync_clinical_trials(config)

        assert "CTG term 'bad'" in "\n".join(result.errors)
        assert result.cached == 1

    async def test_separates_no_nct_from_no_drug_counters(self, mocker, caplog):
        # One study missing NCT, one study missing drug — each should hit its
        # own counter and emit its own log line.
        studies = [
            {"protocolSection": {"identificationModule": {}}},  # no NCT
            _make_study(
                nct_id="NCT_NO_DRUG",
                interventions=[{"type": "BEHAVIORAL", "name": "edu"}],
            ),
        ]
        mocker.patch(
            "pipeline.clinical_trials_fetch.fetch_csvd_studies",
            return_value=(studies, []),
        )
        mocker.patch(
            "pipeline.database.upsert_clinical_trials_batch",
            new_callable=AsyncMock,
            return_value=0,
        )
        import logging

        caplog.set_level(logging.DEBUG, logger="pipeline.clinical_trials_fetch")
        config = PipelineConfig()
        await sync_clinical_trials(config)

        messages = [rec.message for rec in caplog.records]
        assert any("missing NCT ID" in m for m in messages)
        assert any("no DRUG-type intervention" in m for m in messages)


# ---------------------------------------------------------------------------
# upsert_clinical_trials_batch — SQL invariant
# ---------------------------------------------------------------------------


class TestUpsertClinicalTrialsBatchSQL:
    """Pin down the load-bearing invariant: curator columns are NOT in SET."""

    @pytest.fixture
    def sample_record(self):
        return ClinicalTrialRecord(
            drug="aspirin",
            trial_name="Title",
            registry_id="NCT1",
            clinical_trial_phase="PHASE2",
            target_sample_size=100,
            estimated_completion_date="2026-12-31",
            primary_outcome="Outcome",
            sponsor_type="INDUSTRY",
        )

    async def test_sql_omits_curator_columns_from_set(self, mocker, sample_record):
        from pipeline.database import upsert_clinical_trials_batch

        captured_sql: list[str] = []

        async def fake_executemany(sql: str, rows):
            captured_sql.append(sql)

        mock_conn = AsyncMock()
        mock_conn.executemany = fake_executemany
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mocker.patch("pipeline.database.Database.connection", return_value=mock_ctx)

        await upsert_clinical_trials_batch([sample_record])

        assert len(captured_sql) == 1
        sql = captured_sql[0]

        # Confirm ON CONFLICT shape
        assert "ON CONFLICT (registry_id, drug) DO UPDATE SET" in sql

        # Isolate the SET clause so we don't false-positive on WHERE/other text
        set_clause = sql.split("DO UPDATE SET", 1)[1]

        # Curator columns must NOT appear in the SET clause
        for curator_col in (
            "mechanism_of_action",
            "genetic_target",
            "genetic_evidence",
            "svd_population",
            "svd_population_details",
        ):
            assert curator_col not in set_clause, (
                f"Curator column {curator_col!r} leaked into UPDATE SET — "
                "curator edits would be clobbered on refresh"
            )

        # API columns MUST appear in SET clause
        for api_col in (
            "trial_name",
            "clinical_trial_phase",
            "target_sample_size",
            "estimated_completion_date",
            "primary_outcome",
            "sponsor_type",
        ):
            assert api_col in set_clause

    async def test_empty_list_short_circuits(self):
        from pipeline.database import upsert_clinical_trials_batch

        assert await upsert_clinical_trials_batch([]) == 0

    async def test_returns_row_count(self, mocker, sample_record):
        from pipeline.database import upsert_clinical_trials_batch

        mock_conn = AsyncMock()
        mock_conn.executemany = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mocker.patch("pipeline.database.Database.connection", return_value=mock_ctx)

        n = await upsert_clinical_trials_batch([sample_record, sample_record])
        assert n == 2


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


class TestConfigValidation:
    """PipelineConfig must reject CT misconfig that would hang or crash later."""

    def test_defaults_accepted(self):
        PipelineConfig()  # no raise

    def test_ct_max_concurrency_zero_rejected(self):
        with pytest.raises(ValueError, match="ct_max_concurrency"):
            PipelineConfig(ct_max_concurrency=0)

    def test_ct_max_concurrency_negative_rejected(self):
        with pytest.raises(ValueError, match="ct_max_concurrency"):
            PipelineConfig(ct_max_concurrency=-1)

    def test_ct_page_size_zero_rejected(self):
        with pytest.raises(ValueError, match="ct_page_size"):
            PipelineConfig(ct_page_size=0)

    def test_ct_page_size_too_large_rejected(self):
        with pytest.raises(ValueError, match="ct_page_size"):
            PipelineConfig(ct_page_size=1001)

    def test_ct_max_retries_negative_rejected(self):
        with pytest.raises(ValueError, match="ct_max_retries"):
            PipelineConfig(ct_max_retries=-1)

    def test_ct_max_retries_zero_accepted(self):
        PipelineConfig(ct_max_retries=0)  # no raise — 0 means "try once, no retry"


# ---------------------------------------------------------------------------
# init_ctg_fetch_state event-loop requirement
# ---------------------------------------------------------------------------


class TestInitState:
    def test_raises_outside_event_loop(self):
        # Called from a sync function with no running loop — must fail fast
        # with a clear message rather than creating an orphan Semaphore that
        # misbehaves at first use.
        from pipeline.clinical_trials_fetch import init_ctg_fetch_state

        with pytest.raises(RuntimeError, match="event loop"):
            init_ctg_fetch_state()

    async def test_idempotent_inside_loop(self):
        from pipeline.clinical_trials_fetch import init_ctg_fetch_state

        init_ctg_fetch_state()
        init_ctg_fetch_state()  # second call is a no-op, not an error
