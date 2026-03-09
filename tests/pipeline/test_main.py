"""Tests for pipeline.main — PMID validation, PaperResult, metadata, run_pipeline."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from pipeline.main import (
    PaperResult,
    _validate_pmid,
    fetch_paper_metadata,
    run_pipeline,
)
from pipeline.quality_metrics import TokenUsage

# ---------------------------------------------------------------------------
# _validate_pmid
# ---------------------------------------------------------------------------


class TestValidatePmid:
    def test_valid_pmid(self):
        assert _validate_pmid("12345678") == "12345678"

    def test_valid_short_pmid(self):
        assert _validate_pmid("1") == "1"

    def test_strips_whitespace(self):
        assert _validate_pmid("  12345678  ") == "12345678"

    def test_invalid_format_letters(self):
        with pytest.raises(ValueError, match="Invalid PMID"):
            _validate_pmid("abc123")

    def test_invalid_format_empty(self):
        with pytest.raises(ValueError, match="Invalid PMID"):
            _validate_pmid("")

    def test_invalid_format_too_long(self):
        with pytest.raises(ValueError, match="Invalid PMID"):
            _validate_pmid("123456789")  # 9 digits

    def test_invalid_format_special_chars(self):
        with pytest.raises(ValueError, match="Invalid PMID"):
            _validate_pmid("1234-5678")

    def test_max_length_pmid(self):
        assert _validate_pmid("12345678") == "12345678"  # 8 digits


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
        mock_client.get = AsyncMock(
            side_effect=httpx.TimeoutException("timeout")
        )
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

        metrics = await run_pipeline(days_back=7)
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

        metrics = await run_pipeline(days_back=7)
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
        metrics = await run_pipeline(days_back=7, test_mode=True)
        # Should proceed with all PMIDs (treating existing as empty)
        assert metrics.papers_processed == 0  # test mode doesn't process
