"""Tests for pipeline.validation — multi-stage validation, NCBI cache."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx

from pipeline.config import PipelineConfig
from pipeline.validation import (
    _fetch_ncbi_gene_uncached,
    clear_gene_cache,
    fetch_gene_details,
    validate_gene_entry,
    verify_ncbi_gene,
)

# ---------------------------------------------------------------------------
# Stage 1: Confidence threshold
# ---------------------------------------------------------------------------


class TestConfidenceValidation:
    async def test_low_confidence_rejected(self, make_gene_entry):
        entry = make_gene_entry(confidence=0.5)
        result = await validate_gene_entry(entry)
        assert not result.is_valid
        assert any("confidence" in e.lower() for e in result.errors)

    async def test_at_threshold_passes_stage1(self, make_gene_entry, mocker):
        entry = make_gene_entry(confidence=0.7)
        mocker.patch(
            "pipeline.validation.verify_ncbi_gene",
            return_value={"symbol": "NOTCH3", "gene_id": "1234"},
        )
        result = await validate_gene_entry(entry)
        assert result.is_valid

    async def test_below_threshold_with_strict_config(self, make_gene_entry):
        entry = make_gene_entry(confidence=0.85)
        cfg = PipelineConfig(confidence_threshold=0.9)
        result = await validate_gene_entry(entry, config=cfg)
        assert not result.is_valid

    async def test_above_threshold_with_strict_config(
        self, make_gene_entry, mocker
    ):
        entry = make_gene_entry(confidence=0.95)
        cfg = PipelineConfig(confidence_threshold=0.9)
        mocker.patch(
            "pipeline.validation.verify_ncbi_gene",
            return_value={"symbol": "NOTCH3", "gene_id": "1234"},
        )
        result = await validate_gene_entry(entry, config=cfg)
        assert result.is_valid


# ---------------------------------------------------------------------------
# Stage 2: NCBI Gene lookup
# ---------------------------------------------------------------------------


class TestNcbiValidation:
    async def test_gene_not_found(self, make_gene_entry, mocker):
        entry = make_gene_entry(confidence=0.9)
        mocker.patch(
            "pipeline.validation.verify_ncbi_gene", return_value=None
        )
        result = await validate_gene_entry(entry)
        assert not result.is_valid
        assert any("not found in NCBI" in e for e in result.errors)

    async def test_gene_found(self, make_gene_entry, mocker):
        entry = make_gene_entry(confidence=0.9)
        mocker.patch(
            "pipeline.validation.verify_ncbi_gene",
            return_value={"symbol": "NOTCH3", "gene_id": "4854"},
        )
        result = await validate_gene_entry(entry)
        assert result.is_valid
        assert result.normalized_data is not None

    async def test_gene_symbol_normalized(self, make_gene_entry, mocker):
        entry = make_gene_entry(gene_symbol="notch3", confidence=0.9)
        mocker.patch(
            "pipeline.validation.verify_ncbi_gene",
            return_value={"symbol": "NOTCH3", "gene_id": "4854"},
        )
        result = await validate_gene_entry(entry)
        assert result.is_valid
        assert result.normalized_data.gene_symbol == "NOTCH3"
        assert any("Normalized" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Stage 3: GWAS trait validation
# ---------------------------------------------------------------------------


class TestGwasTraitValidation:
    async def test_valid_traits_no_warning(self, make_gene_entry, mocker):
        entry = make_gene_entry(gwas_trait=["WMH", "SVS"], confidence=0.9)
        mocker.patch(
            "pipeline.validation.verify_ncbi_gene",
            return_value={"symbol": "NOTCH3", "gene_id": "4854"},
        )
        result = await validate_gene_entry(entry)
        assert result.is_valid
        assert not any("Unknown GWAS" in w for w in result.warnings)

    async def test_unknown_trait_warns(self, make_gene_entry, mocker):
        entry = make_gene_entry(
            gwas_trait=["WMH", "FAKE_TRAIT"], confidence=0.9
        )
        mocker.patch(
            "pipeline.validation.verify_ncbi_gene",
            return_value={"symbol": "NOTCH3", "gene_id": "4854"},
        )
        result = await validate_gene_entry(entry)
        assert result.is_valid  # warnings don't block
        assert any("Unknown GWAS trait: FAKE_TRAIT" in w for w in result.warnings)

    async def test_empty_traits_no_warning(self, make_gene_entry, mocker):
        entry = make_gene_entry(gwas_trait=[], confidence=0.9)
        mocker.patch(
            "pipeline.validation.verify_ncbi_gene",
            return_value={"symbol": "NOTCH3", "gene_id": "4854"},
        )
        result = await validate_gene_entry(entry)
        assert result.is_valid


# ---------------------------------------------------------------------------
# verify_ncbi_gene caching
# ---------------------------------------------------------------------------


class TestVerifyNcbiGeneCache:
    async def test_cache_hit(self, mocker):
        import pipeline.validation as val

        val._gene_cache["NOTCH3"] = {"symbol": "NOTCH3", "gene_id": "4854"}

        mock_fetch = mocker.patch(
            "pipeline.validation._fetch_ncbi_gene_uncached"
        )
        result = await verify_ncbi_gene("NOTCH3")

        assert result is not None
        assert result["symbol"] == "NOTCH3"
        mock_fetch.assert_not_called()

    async def test_cache_miss_fetches(self, mocker):
        mocker.patch(
            "pipeline.validation._fetch_ncbi_gene_uncached",
            return_value={"symbol": "HTRA1", "gene_id": "5654"},
        )
        result = await verify_ncbi_gene("HTRA1")
        assert result is not None
        assert result["symbol"] == "HTRA1"

    async def test_case_insensitive_cache(self, mocker):
        import pipeline.validation as val

        val._gene_cache["NOTCH3"] = {"symbol": "NOTCH3", "gene_id": "4854"}

        mock_fetch = mocker.patch(
            "pipeline.validation._fetch_ncbi_gene_uncached"
        )
        result = await verify_ncbi_gene("notch3")
        assert result is not None
        mock_fetch.assert_not_called()

    async def test_cache_stores_none(self, mocker):
        import pipeline.validation as val

        mocker.patch(
            "pipeline.validation._fetch_ncbi_gene_uncached",
            return_value=None,
        )
        result = await verify_ncbi_gene("FAKEGENE")
        assert result is None
        assert val._gene_cache.get("FAKEGENE") is None

    def test_clear_gene_cache(self):
        import pipeline.validation as val

        val._gene_cache["TEST"] = {"symbol": "TEST"}
        clear_gene_cache()
        assert val._gene_cache == {}


# ---------------------------------------------------------------------------
# _fetch_ncbi_gene_uncached — HTTP mocking
# ---------------------------------------------------------------------------


class TestFetchNcbiGene:
    async def test_successful_lookup(self, mocker):
        mock_client = AsyncMock()

        # Mock esearch response (use MagicMock so .json() is sync)
        search_response = MagicMock()
        search_response.status_code = 200
        search_response.json.return_value = {
            "esearchresult": {"count": "1", "idlist": ["4854"]}
        }

        # Mock esummary response
        summary_response = MagicMock()
        summary_response.status_code = 200
        summary_response.json.return_value = {
            "result": {
                "4854": {
                    "name": "NOTCH3",
                    "description": "notch receptor 3",
                    "chromosome": "19",
                    "otheraliases": "CADASIL",
                }
            }
        }

        mock_client.get = AsyncMock(
            side_effect=[search_response, summary_response]
        )
        mocker.patch(
            "pipeline.validation._get_http_client",
            return_value=mock_client,
        )

        result = await _fetch_ncbi_gene_uncached("NOTCH3")
        assert result is not None
        assert result["symbol"] == "NOTCH3"
        assert result["gene_id"] == "4854"

    async def test_gene_not_found(self, mocker):
        mock_client = AsyncMock()
        search_response = MagicMock()
        search_response.status_code = 200
        search_response.json.return_value = {
            "esearchresult": {"count": "0", "idlist": []}
        }
        mock_client.get = AsyncMock(return_value=search_response)
        mocker.patch(
            "pipeline.validation._get_http_client",
            return_value=mock_client,
        )

        result = await _fetch_ncbi_gene_uncached("FAKEGENE")
        assert result is None

    async def test_timeout_returns_none(self, mocker):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        mocker.patch(
            "pipeline.validation._get_http_client",
            return_value=mock_client,
        )

        result = await _fetch_ncbi_gene_uncached("NOTCH3")
        assert result is None

    async def test_request_error_returns_none(self, mocker):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=httpx.RequestError("connection failed")
        )
        mocker.patch(
            "pipeline.validation._get_http_client",
            return_value=mock_client,
        )

        result = await _fetch_ncbi_gene_uncached("NOTCH3")
        assert result is None

    async def test_http_error_returns_none(self, mocker):
        mock_client = AsyncMock()
        response = MagicMock()
        response.status_code = 500
        mock_client.get = AsyncMock(return_value=response)
        mocker.patch(
            "pipeline.validation._get_http_client",
            return_value=mock_client,
        )

        result = await _fetch_ncbi_gene_uncached("NOTCH3")
        assert result is None


# ---------------------------------------------------------------------------
# fetch_gene_details
# ---------------------------------------------------------------------------


class TestFetchGeneDetails:
    async def test_successful_fetch(self, mocker):
        mock_client = AsyncMock()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "result": {
                "4854": {
                    "name": "NOTCH3",
                    "description": "notch receptor 3",
                    "chromosome": "19",
                    "otheraliases": "CADASIL, CASIL",
                }
            }
        }
        mock_client.get = AsyncMock(return_value=response)
        mocker.patch(
            "pipeline.validation._get_http_client",
            return_value=mock_client,
        )

        result = await fetch_gene_details("4854")
        assert result is not None
        assert result["symbol"] == "NOTCH3"
        assert "CADASIL" in result["aliases"]

    async def test_error_in_response(self, mocker):
        mock_client = AsyncMock()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "result": {"4854": {"error": "cannot get gene info"}}
        }
        mock_client.get = AsyncMock(return_value=response)
        mocker.patch(
            "pipeline.validation._get_http_client",
            return_value=mock_client,
        )

        result = await fetch_gene_details("4854")
        assert result is None

    async def test_no_aliases(self, mocker):
        mock_client = AsyncMock()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "result": {
                "4854": {
                    "name": "NOTCH3",
                    "description": "notch receptor 3",
                    "chromosome": "19",
                    "otheraliases": "",
                }
            }
        }
        mock_client.get = AsyncMock(return_value=response)
        mocker.patch(
            "pipeline.validation._get_http_client",
            return_value=mock_client,
        )

        result = await fetch_gene_details("4854")
        assert result is not None
        assert result["aliases"] == []
