"""Tests for pipeline.pdf_retrieval — fulltext cascade, validation helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from pipeline.pdf_retrieval import (
    DOI_PATTERN,
    PMID_PATTERN,
    _validate_doi,
    _validate_pmid,
    check_unpaywall,
    get_fulltext,
)

# ---------------------------------------------------------------------------
# _validate_pmid
# ---------------------------------------------------------------------------


class TestValidatePmid:
    def test_valid_pmid(self):
        assert _validate_pmid("12345678") == "12345678"

    def test_valid_short(self):
        assert _validate_pmid("1") == "1"

    def test_strips_whitespace(self):
        assert _validate_pmid("  123  ") == "123"

    def test_invalid_letters(self):
        with pytest.raises(ValueError, match="Invalid PMID"):
            _validate_pmid("abc")

    def test_invalid_empty(self):
        with pytest.raises(ValueError, match="Invalid PMID"):
            _validate_pmid("")

    def test_invalid_too_long(self):
        with pytest.raises(ValueError, match="Invalid PMID"):
            _validate_pmid("123456789")


# ---------------------------------------------------------------------------
# _validate_doi
# ---------------------------------------------------------------------------


class TestValidateDoi:
    def test_valid_doi(self):
        assert _validate_doi("10.1234/test.123") == "10.1234/test.123"

    def test_strips_whitespace(self):
        assert _validate_doi("  10.1234/test  ") == "10.1234/test"

    def test_invalid_format(self):
        with pytest.raises(ValueError, match="Invalid DOI"):
            _validate_doi("not-a-doi")

    def test_invalid_empty(self):
        with pytest.raises(ValueError, match="Invalid DOI"):
            _validate_doi("")


# ---------------------------------------------------------------------------
# get_fulltext cascade
# ---------------------------------------------------------------------------


class TestGetFulltext:
    async def test_pmc_first(self, mocker):
        mocker.patch(
            "pipeline.pdf_retrieval.fetch_pmc_fulltext",
            return_value="Full text from PMC",
        )
        result = await get_fulltext("12345678", "10.1234/test")
        assert result["source"] == "pmc"
        assert result["fulltext"] is True
        assert result["text"] == "Full text from PMC"

    async def test_unpaywall_fallback(self, mocker):
        mocker.patch(
            "pipeline.pdf_retrieval.fetch_pmc_fulltext", return_value=None
        )
        mocker.patch(
            "pipeline.pdf_retrieval.check_unpaywall",
            return_value="https://example.com/paper.pdf",
        )
        mocker.patch(
            "pipeline.pdf_retrieval.download_and_parse_pdf",
            return_value="PDF text",
        )
        result = await get_fulltext("12345678", "10.1234/test")
        assert result["source"] == "unpaywall"
        assert result["fulltext"] is True

    async def test_abstract_fallback(self, mocker):
        mocker.patch(
            "pipeline.pdf_retrieval.fetch_pmc_fulltext", return_value=None
        )
        mocker.patch(
            "pipeline.pdf_retrieval.check_unpaywall", return_value=None
        )
        mocker.patch(
            "pipeline.pdf_retrieval.fetch_abstract",
            return_value="Abstract text",
        )
        result = await get_fulltext("12345678", "10.1234/test")
        assert result["source"] == "abstract"
        assert result["fulltext"] is False
        assert result["text"] == "Abstract text"

    async def test_no_doi_skips_unpaywall(self, mocker):
        mocker.patch(
            "pipeline.pdf_retrieval.fetch_pmc_fulltext", return_value=None
        )
        mock_unpaywall = mocker.patch("pipeline.pdf_retrieval.check_unpaywall")
        mocker.patch(
            "pipeline.pdf_retrieval.fetch_abstract", return_value="Abstract"
        )

        result = await get_fulltext("12345678", None)
        mock_unpaywall.assert_not_called()
        assert result["source"] == "abstract"

    async def test_invalid_pmid_raises(self):
        with pytest.raises(ValueError, match="Invalid PMID"):
            await get_fulltext("invalid", None)

    async def test_invalid_doi_falls_through(self, mocker):
        """Invalid DOI should not crash — falls through to abstract."""
        mocker.patch(
            "pipeline.pdf_retrieval.fetch_pmc_fulltext", return_value=None
        )
        mocker.patch(
            "pipeline.pdf_retrieval.fetch_abstract",
            return_value="Abstract",
        )
        result = await get_fulltext("12345678", "not-a-doi")
        assert result["source"] == "abstract"


# ---------------------------------------------------------------------------
# check_unpaywall
# ---------------------------------------------------------------------------


class TestCheckUnpaywall:
    async def test_no_email_returns_none(self, mocker):
        mocker.patch("pipeline.pdf_retrieval.UNPAYWALL_EMAIL", "")
        result = await check_unpaywall("10.1234/test")
        assert result is None

    async def test_successful_oa(self, mocker):
        mocker.patch("pipeline.pdf_retrieval.UNPAYWALL_EMAIL", "test@test.com")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "is_oa": True,
            "best_oa_location": {"url_for_pdf": "https://example.com/paper.pdf"},
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mocker.patch(
            "pipeline.pdf_retrieval._get_http_client",
            return_value=mock_client,
        )

        result = await check_unpaywall("10.1234/test")
        assert result == "https://example.com/paper.pdf"

    async def test_not_oa(self, mocker):
        mocker.patch("pipeline.pdf_retrieval.UNPAYWALL_EMAIL", "test@test.com")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"is_oa": False}
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mocker.patch(
            "pipeline.pdf_retrieval._get_http_client",
            return_value=mock_client,
        )

        result = await check_unpaywall("10.1234/test")
        assert result is None

    async def test_timeout(self, mocker):
        mocker.patch("pipeline.pdf_retrieval.UNPAYWALL_EMAIL", "test@test.com")
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=httpx.TimeoutException("timeout")
        )
        mocker.patch(
            "pipeline.pdf_retrieval._get_http_client",
            return_value=mock_client,
        )

        result = await check_unpaywall("10.1234/test")
        assert result is None


# ---------------------------------------------------------------------------
# PMID/DOI regex patterns
# ---------------------------------------------------------------------------


class TestPatterns:
    def test_pmid_pattern_valid(self):
        assert PMID_PATTERN.match("12345678")
        assert PMID_PATTERN.match("1")

    def test_pmid_pattern_invalid(self):
        assert not PMID_PATTERN.match("123456789")
        assert not PMID_PATTERN.match("abc")
        assert not PMID_PATTERN.match("")

    def test_doi_pattern_valid(self):
        assert DOI_PATTERN.match("10.1234/test")
        assert DOI_PATTERN.match("10.12345/test.v1")

    def test_doi_pattern_invalid(self):
        assert not DOI_PATTERN.match("not-a-doi")
        assert not DOI_PATTERN.match("")
