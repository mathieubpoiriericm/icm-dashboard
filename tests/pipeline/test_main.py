"""Tests for pipeline.main — PMID validation, PaperResult, metadata, run_pipeline."""

from __future__ import annotations

import argparse
from unittest.mock import AsyncMock

import httpx
import pytest

from pipeline.config import PipelineConfig
from pipeline.main import (
    ExtractionFailedError,
    PaperResult,
    _build_parser,
    _run_selected_pipelines,
    _validate_genes,
    fetch_paper_metadata,
    run_pipeline,
)
from pipeline.quality_metrics import PipelineMetrics, TokenUsage
from pipeline.validation import ValidationResult

# ---------------------------------------------------------------------------
# _validate_genes — None guard (Bug 5)
# ---------------------------------------------------------------------------


class TestValidateGenesNoneGuard:
    async def test_none_normalized_data_skipped(self, make_gene_entry, mocker):
        """Bug 5: is_valid=True with normalized_data=None must not crash."""
        gene = make_gene_entry(confidence=0.9)
        # Return a valid result but with None normalized_data
        mocker.patch(
            "pipeline.main.validate_gene_entry",
            return_value=ValidationResult(
                is_valid=True, errors=[], warnings=[], normalized_data=None
            ),
        )
        config = PipelineConfig()
        metrics = PipelineMetrics()
        validated, rejected = await _validate_genes([gene], metrics, config)
        assert validated == []
        # Gene with None data should not be counted as validated
        assert metrics.genes_validated == 0

    async def test_valid_normalized_data_included(self, make_gene_entry, mocker):
        """Normal case: is_valid=True with actual normalized_data."""
        gene = make_gene_entry(confidence=0.9)
        mocker.patch(
            "pipeline.main.validate_gene_entry",
            return_value=ValidationResult(
                is_valid=True, errors=[], warnings=[], normalized_data=gene
            ),
        )
        config = PipelineConfig()
        metrics = PipelineMetrics()
        validated, rejected = await _validate_genes([gene], metrics, config)
        assert len(validated) == 1
        assert metrics.genes_validated == 1


# ---------------------------------------------------------------------------
# PaperResult
# ---------------------------------------------------------------------------


class TestPaperResult:
    def test_default_values(self):
        r = PaperResult(pmid="111")
        assert r.genes == []
        assert r.fulltext is False
        assert r.source == "none"
        assert r.error is None
        assert r.succeeded is True

    def test_with_error(self):
        r = PaperResult(pmid="111", error="Something failed")
        assert not r.succeeded
        assert r.error == "Something failed"

    def test_with_genes(self):
        from pipeline.llm_extraction import GeneEntry

        gene = GeneEntry(gene_symbol="NOTCH3", confidence=0.9)
        r = PaperResult(pmid="111", genes=[gene])
        assert len(r.genes) == 1

    def test_with_token_usage(self):
        tu = TokenUsage(input_tokens=100, output_tokens=50)
        r = PaperResult(pmid="111", token_usage=tu)
        assert r.token_usage.total_tokens == 150


# ---------------------------------------------------------------------------
# fetch_paper_metadata
# ---------------------------------------------------------------------------


class TestFetchPaperMetadata:
    async def test_successful_fetch(self, mocker):
        xml_response = b"""<?xml version="1.0"?>
        <PubmedArticleSet>
            <PubmedArticle>
                <PubmedData>
                    <ArticleIdList>
                        <ArticleId IdType="doi">10.1234/test</ArticleId>
                    </ArticleIdList>
                </PubmedData>
            </PubmedArticle>
        </PubmedArticleSet>"""

        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_resp.content = xml_response

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mocker.patch(
            "pipeline.main._get_metadata_client",
            return_value=mock_client,
        )

        result = await fetch_paper_metadata("12345678")
        assert result["pmid"] == "12345678"
        assert result["doi"] == "10.1234/test"

    async def test_no_doi_in_response(self, mocker):
        xml_response = b"""<?xml version="1.0"?>
        <PubmedArticleSet>
            <PubmedArticle>
                <PubmedData>
                    <ArticleIdList>
                        <ArticleId IdType="pubmed">12345678</ArticleId>
                    </ArticleIdList>
                </PubmedData>
            </PubmedArticle>
        </PubmedArticleSet>"""

        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_resp.content = xml_response

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mocker.patch(
            "pipeline.main._get_metadata_client",
            return_value=mock_client,
        )

        result = await fetch_paper_metadata("12345678")
        assert result["doi"] is None

    async def test_http_error(self, mocker):
        mock_resp = AsyncMock()
        mock_resp.status_code = 500

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mocker.patch(
            "pipeline.main._get_metadata_client",
            return_value=mock_client,
        )

        result = await fetch_paper_metadata("12345678")
        assert result["doi"] is None

    async def test_timeout(self, mocker):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        mocker.patch(
            "pipeline.main._get_metadata_client",
            return_value=mock_client,
        )

        result = await fetch_paper_metadata("12345678")
        assert result["pmid"] == "12345678"
        assert result["doi"] is None

    async def test_invalid_pmid(self):
        with pytest.raises(ValueError, match="Invalid PMID"):
            await fetch_paper_metadata("invalid_pmid")


# ---------------------------------------------------------------------------
# run_pipeline
# ---------------------------------------------------------------------------


class TestRunPipeline:
    async def test_invalid_days_back_too_low(self):
        with pytest.raises(ValueError, match="days_back must be"):
            await run_pipeline(days_back=0)

    async def test_invalid_days_back_too_high(self):
        with pytest.raises(ValueError, match="days_back must be"):
            await run_pipeline(days_back=99999)

    async def test_no_papers_found(self, mocker):
        mocker.patch("pipeline.main.search_recent_papers", return_value=[])
        mocker.patch("pipeline.main._close_metadata_client", new_callable=AsyncMock)
        mocker.patch("pipeline.main.close_http_client", new_callable=AsyncMock)
        mocker.patch("pipeline.main.close_validation_client", new_callable=AsyncMock)
        mocker.patch("pipeline.main.Database.close", new_callable=AsyncMock)
        mocker.patch("pipeline.main.clear_gene_cache")

        metrics, _ = await run_pipeline(days_back=7)
        assert metrics.papers_processed == 0

    async def test_all_papers_already_processed(self, mocker):
        mocker.patch(
            "pipeline.main.search_recent_papers",
            return_value=["111", "222"],
        )
        mocker.patch(
            "pipeline.main.get_existing_pmids",
            new_callable=AsyncMock,
            return_value={"111", "222"},
        )
        mocker.patch("pipeline.main._close_metadata_client", new_callable=AsyncMock)
        mocker.patch("pipeline.main.close_http_client", new_callable=AsyncMock)
        mocker.patch("pipeline.main.close_validation_client", new_callable=AsyncMock)
        mocker.patch("pipeline.main.Database.close", new_callable=AsyncMock)
        mocker.patch("pipeline.main.clear_gene_cache")

        metrics, _ = await run_pipeline(days_back=7)
        assert metrics.papers_processed == 0

    async def test_test_mode_skips_extraction(self, mocker):
        mocker.patch(
            "pipeline.main.search_recent_papers",
            return_value=["111"],
        )
        mocker.patch(
            "pipeline.main.get_existing_pmids",
            new_callable=AsyncMock,
            return_value=set(),
        )
        mocker.patch("pipeline.main._close_metadata_client", new_callable=AsyncMock)
        mocker.patch("pipeline.main.close_http_client", new_callable=AsyncMock)
        mocker.patch("pipeline.main.close_validation_client", new_callable=AsyncMock)
        mocker.patch("pipeline.main.Database.close", new_callable=AsyncMock)
        mocker.patch("pipeline.main.clear_gene_cache")

        # Should not call extract_from_paper
        mock_extract = mocker.patch("pipeline.main.extract_from_paper")

        await run_pipeline(days_back=7, test_mode=True)
        mock_extract.assert_not_called()

    async def test_extraction_failure_not_recorded_as_processed(self, mocker):
        mocker.patch("pipeline.main.search_recent_papers", return_value=["111"])
        mocker.patch(
            "pipeline.main.get_existing_pmids",
            new_callable=AsyncMock,
            return_value=set(),
        )
        mocker.patch(
            "pipeline.main.fetch_paper_metadata",
            new_callable=AsyncMock,
            return_value={"pmid": "111", "doi": None},
        )
        mocker.patch(
            "pipeline.main.get_fulltext",
            new_callable=AsyncMock,
            return_value={
                "text": "paper text",
                "source": "abstract",
                "fulltext": False,
            },
        )
        mocker.patch(
            "pipeline.main.extract_from_paper",
            new_callable=AsyncMock,
            side_effect=ExtractionFailedError(
                "provider failed",
                TokenUsage(input_tokens=10, output_tokens=5),
            ),
        )
        mocker.patch("pipeline.main.reset_sequence", new_callable=AsyncMock)
        mocker.patch("pipeline.main.merge_gene_entries", new_callable=AsyncMock)
        mock_record_pmids = mocker.patch(
            "pipeline.main.record_processed_pmids_batch",
            new_callable=AsyncMock,
            return_value=0,
        )
        mocker.patch("pipeline.main.record_pipeline_run", new_callable=AsyncMock)
        mocker.patch("pipeline.main.write_comprehensive_report")
        mocker.patch("pipeline.main.print_rich_summary")
        mocker.patch("pipeline.main._record_and_notify", new_callable=AsyncMock)
        mocker.patch("pipeline.main.ping_start", new_callable=AsyncMock)
        mocker.patch("pipeline.main.ping_success", new_callable=AsyncMock)
        mocker.patch("pipeline.main._close_metadata_client", new_callable=AsyncMock)
        mocker.patch("pipeline.main.close_http_client", new_callable=AsyncMock)
        mocker.patch("pipeline.main.close_validation_client", new_callable=AsyncMock)
        mocker.patch("pipeline.main.close_async_client", new_callable=AsyncMock)
        mocker.patch("pipeline.main.close_healthcheck_client", new_callable=AsyncMock)
        mocker.patch("pipeline.main.Database.close", new_callable=AsyncMock)
        mocker.patch("pipeline.main.clear_gene_cache")

        metrics, run_data = await run_pipeline(days_back=7)

        assert metrics.papers_processed == 0
        assert metrics.token_usage.total_tokens == 15
        assert run_data is not None
        assert run_data["papers"]["failed"] == 1
        mock_record_pmids.assert_awaited_once_with([])

    async def test_existing_pmids_error_treated_as_empty(self, mocker):
        mocker.patch(
            "pipeline.main.search_recent_papers",
            return_value=["111"],
        )
        mocker.patch(
            "pipeline.main.get_existing_pmids",
            new_callable=AsyncMock,
            side_effect=Exception("DB not available"),
        )
        mocker.patch("pipeline.main._close_metadata_client", new_callable=AsyncMock)
        mocker.patch("pipeline.main.close_http_client", new_callable=AsyncMock)
        mocker.patch("pipeline.main.close_validation_client", new_callable=AsyncMock)
        mocker.patch("pipeline.main.Database.close", new_callable=AsyncMock)
        mocker.patch("pipeline.main.clear_gene_cache")

        # test_mode to avoid LLM calls
        metrics, _ = await run_pipeline(days_back=7, test_mode=True)
        # Should proceed with all PMIDs (treating existing as empty)
        assert metrics.papers_processed == 0  # test mode doesn't process


# ---------------------------------------------------------------------------
# CLI parser — new pipeline selector flags
# ---------------------------------------------------------------------------


class TestCliParser:
    def test_pubmed_flag(self):
        args = _build_parser().parse_args(["--pubmed"])
        assert args.pubmed is True
        assert args.clinical_trials is False
        assert args.sync_external_data is False

    def test_clinical_trials_flag(self):
        args = _build_parser().parse_args(["--clinical-trials"])
        assert args.clinical_trials is True
        assert args.pubmed is False
        assert args.sync_external_data is False

    def test_combine_pubmed_and_clinical_trials(self):
        args = _build_parser().parse_args(["--pubmed", "--clinical-trials"])
        assert args.pubmed is True
        assert args.clinical_trials is True

    def test_combine_all_three_online_flags(self):
        args = _build_parser().parse_args(
            ["--pubmed", "--clinical-trials", "--sync-external-data"]
        )
        assert args.pubmed is True
        assert args.clinical_trials is True
        assert args.sync_external_data is True

    def test_no_flags_defaults_false(self):
        args = _build_parser().parse_args([])
        # All selector flags default False at the parser level;
        # main() promotes --pubmed when nothing else is selected.
        assert args.pubmed is False
        assert args.clinical_trials is False
        assert args.sync_external_data is False


# ---------------------------------------------------------------------------
# _run_selected_pipelines — multi-pipeline dispatcher
# ---------------------------------------------------------------------------


def _make_dispatcher_args(**overrides):
    defaults = {
        "pubmed": False,
        "clinical_trials": False,
        "sync_external_data": False,
        "days_back": 7,
        "dry_run": False,
        "test_mode": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestRunSelectedPipelines:
    async def test_pubmed_only_calls_run_pipeline(self, mocker):
        mock_run = mocker.patch("pipeline.main.run_pipeline", new_callable=AsyncMock)
        mock_run.return_value = (PipelineMetrics(), {"pipeline_config": {"mode": None}})
        mocker.patch("pipeline.main.ping_start")
        mocker.patch("pipeline.main.ping_success")
        mocker.patch("pipeline.main.ping_failure")
        mocker.patch("pipeline.main._record_and_notify")
        mocker.patch("pipeline.main.Database.close", new_callable=AsyncMock)

        args = _make_dispatcher_args(pubmed=True)
        exit_code = await _run_selected_pipelines(args, PipelineConfig())

        assert exit_code == 0
        mock_run.assert_awaited_once()
        # Dispatcher must tell run_pipeline to skip its own lifecycle.
        assert mock_run.await_args.kwargs["manage_lifecycle"] is False

    async def test_clinical_trials_only(self, mocker):
        mock_ct = mocker.patch(
            "pipeline.main.run_clinical_trials_pipeline", new_callable=AsyncMock
        )
        mock_ct.return_value = {
            "name": "clinical_trials",
            "status": "ok",
            "metrics": {"fetched": 1, "cached": 1, "failed": 0},
            "errors": [],
        }
        mock_run = mocker.patch("pipeline.main.run_pipeline", new_callable=AsyncMock)
        mocker.patch("pipeline.main.ping_start")
        mocker.patch("pipeline.main.ping_success")
        mocker.patch("pipeline.main.ping_failure")
        mocker.patch("pipeline.main._record_and_notify")
        mocker.patch("pipeline.main.Database.close", new_callable=AsyncMock)

        args = _make_dispatcher_args(clinical_trials=True)
        exit_code = await _run_selected_pipelines(args, PipelineConfig())

        assert exit_code == 0
        mock_ct.assert_awaited_once()
        mock_run.assert_not_awaited()

    async def test_pubmed_and_clinical_trials_run_sequentially(self, mocker):
        mock_run = mocker.patch("pipeline.main.run_pipeline", new_callable=AsyncMock)
        mock_run.return_value = (PipelineMetrics(), None)
        mock_ct = mocker.patch(
            "pipeline.main.run_clinical_trials_pipeline", new_callable=AsyncMock
        )
        mock_ct.return_value = {
            "name": "clinical_trials",
            "status": "ok",
            "metrics": {},
            "errors": [],
        }
        mocker.patch("pipeline.main.ping_start")
        mocker.patch("pipeline.main.ping_success")
        mocker.patch("pipeline.main.ping_failure")
        mocker.patch("pipeline.main._record_and_notify")
        mocker.patch("pipeline.main.Database.close", new_callable=AsyncMock)

        args = _make_dispatcher_args(pubmed=True, clinical_trials=True)
        exit_code = await _run_selected_pipelines(args, PipelineConfig())

        assert exit_code == 0
        mock_run.assert_awaited_once()
        mock_ct.assert_awaited_once()

    async def test_pubmed_failure_still_runs_clinical_trials(self, mocker):
        """Continue-on-error: one pipeline's failure doesn't skip the next."""
        mock_run = mocker.patch("pipeline.main.run_pipeline", new_callable=AsyncMock)
        mock_run.side_effect = RuntimeError("pubmed exploded")
        mock_ct = mocker.patch(
            "pipeline.main.run_clinical_trials_pipeline", new_callable=AsyncMock
        )
        mock_ct.return_value = {
            "name": "clinical_trials",
            "status": "ok",
            "metrics": {},
            "errors": [],
        }
        mocker.patch("pipeline.main.ping_start")
        mocker.patch("pipeline.main.ping_success")
        mock_fail = mocker.patch("pipeline.main.ping_failure")
        mocker.patch("pipeline.main._record_and_notify")
        mocker.patch("pipeline.main.Database.close", new_callable=AsyncMock)

        args = _make_dispatcher_args(pubmed=True, clinical_trials=True)
        exit_code = await _run_selected_pipelines(args, PipelineConfig())

        assert exit_code == 1  # failure reported
        mock_ct.assert_awaited_once()  # still ran CT after PubMed failed
        mock_fail.assert_called_once()
