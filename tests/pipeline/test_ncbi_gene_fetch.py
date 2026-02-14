"""Tests for pipeline.ncbi_gene_fetch — NCBI Gene information fetching."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx

from pipeline.ncbi_gene_fetch import (
    NCBIGeneInfo,
    SyncResult,
    _fetch_gene_summary,
    _fetch_ncbi_gene_uncached,
    clear_ncbi_cache,
    fetch_ncbi_gene_info,
    fetch_ncbi_genes_batch,
    sync_ncbi_gene_info,
)

# ---------------------------------------------------------------------------
# NCBIGeneInfo dataclass
# ---------------------------------------------------------------------------


class TestNCBIGeneInfo:
    def test_field_access(self):
        info = NCBIGeneInfo(
            gene_symbol="NOTCH3",
            ncbi_uid="4854",
            description="notch receptor 3",
            aliases="CADASIL, CASIL",
        )
        assert info.gene_symbol == "NOTCH3"
        assert info.ncbi_uid == "4854"
        assert info.description == "notch receptor 3"
        assert info.aliases == "CADASIL, CASIL"

    def test_none_fields(self):
        info = NCBIGeneInfo(
            gene_symbol="FAKE", ncbi_uid=None, description=None, aliases=None
        )
        assert info.ncbi_uid is None
        assert info.description is None
        assert info.aliases is None


# ---------------------------------------------------------------------------
# clear_ncbi_cache
# ---------------------------------------------------------------------------


class TestClearCache:
    def test_clears_cache(self):
        import pipeline.ncbi_gene_fetch as mod

        mod._gene_cache["TEST"] = NCBIGeneInfo("TEST", "1", None, None)
        assert "TEST" in mod._gene_cache
        clear_ncbi_cache()
        assert mod._gene_cache == {}


# ---------------------------------------------------------------------------
# fetch_ncbi_gene_info (cached wrapper)
# ---------------------------------------------------------------------------


class TestFetchNCBIGeneInfo:
    async def test_cache_hit(self):
        import pipeline.ncbi_gene_fetch as mod

        cached = NCBIGeneInfo("NOTCH3", "4854", "desc", "alias")
        mod._gene_cache["NOTCH3"] = cached

        result = await fetch_ncbi_gene_info("NOTCH3")
        assert result is cached

    async def test_cache_case_insensitive(self):
        import pipeline.ncbi_gene_fetch as mod

        cached = NCBIGeneInfo("notch3", "4854", "desc", "alias")
        mod._gene_cache["NOTCH3"] = cached

        result = await fetch_ncbi_gene_info("notch3")
        assert result is cached

    async def test_cache_miss_calls_uncached(self, mocker):
        expected = NCBIGeneInfo("HTRA1", "5654", "desc", None)
        mocker.patch(
            "pipeline.ncbi_gene_fetch._fetch_ncbi_gene_uncached",
            return_value=expected,
        )
        result = await fetch_ncbi_gene_info("HTRA1")
        assert result is expected

    async def test_none_results_are_cached(self, mocker):
        mocker.patch(
            "pipeline.ncbi_gene_fetch._fetch_ncbi_gene_uncached",
            return_value=None,
        )
        result = await fetch_ncbi_gene_info("MISSING")
        assert result is None

        import pipeline.ncbi_gene_fetch as mod

        assert "MISSING" in mod._gene_cache
        assert mod._gene_cache["MISSING"] is None


# ---------------------------------------------------------------------------
# _fetch_ncbi_gene_uncached
# ---------------------------------------------------------------------------


class TestFetchNCBIGeneUncached:
    async def test_successful_2step_flow(self, mocker):
        """esearch finds gene ID, esummary returns details."""
        search_resp = httpx.Response(
            200,
            json={"esearchresult": {"count": "1", "idlist": ["4854"]}},
        )
        summary_info = NCBIGeneInfo("NOTCH3", "4854", "notch receptor 3", "CADASIL")

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=search_resp)
        mocker.patch(
            "pipeline.ncbi_gene_fetch._client_manager.get",
            return_value=mock_client,
        )
        mocker.patch(
            "pipeline.ncbi_gene_fetch._fetch_gene_summary",
            return_value=summary_info,
        )

        result = await _fetch_ncbi_gene_uncached("NOTCH3")
        assert result is summary_info

    async def test_gene_not_found_count_zero(self, mocker):
        """esearch returns count=0 → NCBIGeneInfo with None uid."""
        resp = httpx.Response(
            200,
            json={"esearchresult": {"count": "0", "idlist": []}},
        )
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=resp)
        mocker.patch(
            "pipeline.ncbi_gene_fetch._client_manager.get",
            return_value=mock_client,
        )

        result = await _fetch_ncbi_gene_uncached("FAKEGENE")
        assert result is not None
        assert result.gene_symbol == "FAKEGENE"
        assert result.ncbi_uid is None

    async def test_non_200_returns_none(self, mocker):
        resp = httpx.Response(500)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=resp)
        mocker.patch(
            "pipeline.ncbi_gene_fetch._client_manager.get",
            return_value=mock_client,
        )

        result = await _fetch_ncbi_gene_uncached("NOTCH3")
        assert result is None

    async def test_timeout_returns_none(self, mocker):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        mocker.patch(
            "pipeline.ncbi_gene_fetch._client_manager.get",
            return_value=mock_client,
        )

        result = await _fetch_ncbi_gene_uncached("NOTCH3")
        assert result is None

    async def test_request_error_returns_none(self, mocker):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.RequestError("connection failed"))
        mocker.patch(
            "pipeline.ncbi_gene_fetch._client_manager.get",
            return_value=mock_client,
        )

        result = await _fetch_ncbi_gene_uncached("NOTCH3")
        assert result is None

    async def test_key_error_in_json_returns_none(self, mocker):
        resp = httpx.Response(200, json={"unexpected": "format"})
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=resp)
        mocker.patch(
            "pipeline.ncbi_gene_fetch._client_manager.get",
            return_value=mock_client,
        )

        result = await _fetch_ncbi_gene_uncached("NOTCH3")
        assert result is None


# ---------------------------------------------------------------------------
# _fetch_gene_summary
# ---------------------------------------------------------------------------


class TestFetchGeneSummary:
    async def test_successful_fetch(self, mocker):
        resp = httpx.Response(
            200,
            json={
                "result": {
                    "4854": {
                        "description": "notch receptor 3",
                        "otheraliases": "CADASIL, CASIL",
                    }
                }
            },
        )
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=resp)
        mocker.patch(
            "pipeline.ncbi_gene_fetch._client_manager.get",
            return_value=mock_client,
        )

        result = await _fetch_gene_summary("NOTCH3", "4854")
        assert result is not None
        assert result.ncbi_uid == "4854"
        assert result.description == "notch receptor 3"
        assert result.aliases == "CADASIL, CASIL"

    async def test_error_field_in_response(self, mocker):
        resp = httpx.Response(
            200,
            json={"result": {"4854": {"error": "gene not found"}}},
        )
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=resp)
        mocker.patch(
            "pipeline.ncbi_gene_fetch._client_manager.get",
            return_value=mock_client,
        )

        result = await _fetch_gene_summary("NOTCH3", "4854")
        assert result is None

    async def test_missing_gene_data(self, mocker):
        resp = httpx.Response(200, json={"result": {}})
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=resp)
        mocker.patch(
            "pipeline.ncbi_gene_fetch._client_manager.get",
            return_value=mock_client,
        )

        result = await _fetch_gene_summary("NOTCH3", "4854")
        assert result is None

    async def test_timeout(self, mocker):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        mocker.patch(
            "pipeline.ncbi_gene_fetch._client_manager.get",
            return_value=mock_client,
        )

        result = await _fetch_gene_summary("NOTCH3", "4854")
        assert result is None


# ---------------------------------------------------------------------------
# fetch_ncbi_genes_batch
# ---------------------------------------------------------------------------


class TestFetchNCBIGenesBatch:
    async def test_empty_list(self):
        result = await fetch_ncbi_genes_batch([])
        assert result == []

    async def test_placeholder_for_failed(self, mocker):
        mocker.patch(
            "pipeline.ncbi_gene_fetch.fetch_ncbi_gene_info",
            return_value=None,
        )
        result = await fetch_ncbi_genes_batch(["FAKE"])
        assert len(result) == 1
        assert result[0].gene_symbol == "FAKE"
        assert result[0].ncbi_uid is None

    async def test_progress_callback(self, mocker):
        info = NCBIGeneInfo("A", "1", None, None)
        mocker.patch(
            "pipeline.ncbi_gene_fetch.fetch_ncbi_gene_info",
            return_value=info,
        )
        calls = []
        await fetch_ncbi_genes_batch(
            ["A", "B"],
            progress_callback=lambda cur, tot: calls.append((cur, tot)),
        )
        assert len(calls) == 2
        assert calls[-1] == (2, 2)


# ---------------------------------------------------------------------------
# sync_ncbi_gene_info
# ---------------------------------------------------------------------------


class TestSyncNCBIGeneInfo:
    async def test_all_cached(self, mocker):
        mocker.patch(
            "pipeline.database.get_cached_ncbi_genes",
            return_value={"NOTCH3": {}, "HTRA1": {}},
        )
        result = await sync_ncbi_gene_info(["NOTCH3", "HTRA1"])
        assert isinstance(result, SyncResult)
        assert result.fetched == 0
        assert result.cached == 2

    async def test_mix_cached_and_new(self, mocker):
        mocker.patch(
            "pipeline.database.get_cached_ncbi_genes",
            return_value={"NOTCH3": {}},
        )
        fetched = NCBIGeneInfo("HTRA1", "5654", "desc", None)
        mocker.patch(
            "pipeline.ncbi_gene_fetch.fetch_ncbi_genes_batch",
            return_value=[fetched],
        )
        mock_upsert = mocker.patch(
            "pipeline.database.upsert_ncbi_genes_batch",
            return_value=1,
        )

        result = await sync_ncbi_gene_info(["NOTCH3", "HTRA1"])
        assert result.cached == 1
        assert result.fetched == 1
        assert result.failed == 0
        mock_upsert.assert_called_once()

    async def test_failed_lookups_stored(self, mocker):
        mocker.patch(
            "pipeline.database.get_cached_ncbi_genes",
            return_value={},
        )
        failed = NCBIGeneInfo("FAKE", None, None, None)
        mocker.patch(
            "pipeline.ncbi_gene_fetch.fetch_ncbi_genes_batch",
            return_value=[failed],
        )
        mock_upsert = mocker.patch(
            "pipeline.database.upsert_ncbi_genes_batch",
            return_value=1,
        )

        result = await sync_ncbi_gene_info(["FAKE"])
        assert result.failed == 1
        assert result.fetched == 0
        assert any("FAKE" in e for e in result.errors)
        # Failed lookups are also upserted
        mock_upsert.assert_called_once()
