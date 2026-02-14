"""Tests for pipeline.uniprot_fetch — UniProt protein information fetching."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx

from pipeline.uniprot_fetch import (
    SyncResult,
    UniProtInfo,
    _clean_go_term,
    clear_uniprot_cache,
    fetch_uniprot_accession,
    fetch_uniprot_batch,
    fetch_uniprot_go_info,
    fetch_uniprot_info,
    sync_uniprot_info,
)

# ---------------------------------------------------------------------------
# _clean_go_term
# ---------------------------------------------------------------------------


class TestCleanGoTerm:
    def test_none_returns_none(self):
        assert _clean_go_term(None) is None

    def test_empty_string_returns_none(self):
        assert _clean_go_term("") is None

    def test_removes_go_id(self):
        result = _clean_go_term("apoptotic process [GO:0006915]")
        assert result == "apoptotic process"

    def test_multiple_go_ids(self):
        result = _clean_go_term("process A [GO:0000001]; process B [GO:0000002]")
        assert "[GO:" not in result
        assert "process A" in result
        assert "process B" in result

    def test_cleans_double_semicolons(self):
        result = _clean_go_term("term1 [GO:0000001];; term2")
        assert ";;" not in result

    def test_cleans_extra_whitespace(self):
        result = _clean_go_term("  term   with   spaces  ")
        assert result == "term with spaces"

    def test_whitespace_only_returns_none(self):
        assert _clean_go_term("   ") is None


# ---------------------------------------------------------------------------
# clear_uniprot_cache
# ---------------------------------------------------------------------------


class TestClearCache:
    def test_clears_cache(self):
        import pipeline.uniprot_fetch as mod

        mod._uniprot_cache["TEST"] = UniProtInfo(
            "TEST", "P00000", None, None, None, None, None
        )
        assert "TEST" in mod._uniprot_cache
        clear_uniprot_cache()
        assert mod._uniprot_cache == {}


# ---------------------------------------------------------------------------
# fetch_uniprot_accession
# ---------------------------------------------------------------------------


class TestFetchUniProtAccession:
    async def test_exact_match_found(self, mocker):
        tsv = (
            "Entry\tGene Names (primary)\tGene Names (synonym)\tProtein names\n"
            "P12345\tNOTCH3\t\tNotch receptor 3\n"
        )
        resp = httpx.Response(200, text=tsv)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=resp)
        mocker.patch(
            "pipeline.uniprot_fetch._client_manager.get",
            return_value=mock_client,
        )

        accession, protein = await fetch_uniprot_accession("NOTCH3")
        assert accession == "P12345"
        assert protein == "Notch receptor 3"

    async def test_falls_back_to_synonym_search(self, mocker):
        no_results = httpx.Response(200, text="Entry\tGene Names (primary)\n")
        synonym_tsv = (
            "Entry\tGene Names (primary)\tGene Names (synonym)\tProtein names\n"
            "Q99999\tNOTCH3\tALIAS\tNotch 3\n"
        )
        synonym_resp = httpx.Response(200, text=synonym_tsv)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[no_results, synonym_resp])
        mocker.patch(
            "pipeline.uniprot_fetch._client_manager.get",
            return_value=mock_client,
        )

        accession, protein = await fetch_uniprot_accession("NOTCH3")
        assert accession == "Q99999"

    async def test_first_result_fallback(self, mocker):
        """When no primary gene matches, returns first row."""
        tsv = (
            "Entry\tGene Names (primary)\tGene Names (synonym)\tProtein names\n"
            "P11111\tOTHER\t\tOther protein\n"
            "P22222\tNOTCH3\t\tNotch 3\n"
        )
        resp = httpx.Response(200, text=tsv)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=resp)
        mocker.patch(
            "pipeline.uniprot_fetch._client_manager.get",
            return_value=mock_client,
        )

        # NOTCH3 is in second row, so exact match returns it
        accession, protein = await fetch_uniprot_accession("NOTCH3")
        assert accession == "P22222"

    async def test_no_results_returns_none_none(self, mocker):
        no_results = httpx.Response(200, text="Entry\tGene Names (primary)\n")
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=no_results)
        mocker.patch(
            "pipeline.uniprot_fetch._client_manager.get",
            return_value=mock_client,
        )

        accession, protein = await fetch_uniprot_accession("FAKEGENE")
        assert accession is None
        assert protein is None

    async def test_non_200_returns_none_none(self, mocker):
        resp = httpx.Response(500)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=resp)
        mocker.patch(
            "pipeline.uniprot_fetch._client_manager.get",
            return_value=mock_client,
        )

        accession, protein = await fetch_uniprot_accession("NOTCH3")
        assert accession is None
        assert protein is None

    async def test_timeout_returns_none_none(self, mocker):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        mocker.patch(
            "pipeline.uniprot_fetch._client_manager.get",
            return_value=mock_client,
        )

        accession, protein = await fetch_uniprot_accession("NOTCH3")
        assert accession is None
        assert protein is None

    async def test_tsv_parsing_error_returns_none_none(self, mocker):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.RequestError("connection reset"))
        mocker.patch(
            "pipeline.uniprot_fetch._client_manager.get",
            return_value=mock_client,
        )

        accession, protein = await fetch_uniprot_accession("NOTCH3")
        assert accession is None
        assert protein is None


# ---------------------------------------------------------------------------
# fetch_uniprot_go_info
# ---------------------------------------------------------------------------


class TestFetchUniProtGoInfo:
    async def test_successful_fetch(self, mocker):
        tsv = (
            "Gene Ontology (biological process)\t"
            "Gene Ontology (molecular function)\t"
            "Gene Ontology (cellular component)\n"
            "apoptotic process [GO:0006915]\t"
            "receptor activity [GO:0004872]\t"
            "membrane [GO:0016020]\n"
        )
        resp = httpx.Response(200, text=tsv)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=resp)
        mocker.patch(
            "pipeline.uniprot_fetch._client_manager.get",
            return_value=mock_client,
        )

        result = await fetch_uniprot_go_info("P12345")
        assert result["biological_process"] == "apoptotic process"
        assert result["molecular_function"] == "receptor activity"
        assert result["cellular_component"] == "membrane"

    async def test_non_200_returns_all_none(self, mocker):
        resp = httpx.Response(404)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=resp)
        mocker.patch(
            "pipeline.uniprot_fetch._client_manager.get",
            return_value=mock_client,
        )

        result = await fetch_uniprot_go_info("P12345")
        assert result["biological_process"] is None
        assert result["molecular_function"] is None
        assert result["cellular_component"] is None

    async def test_fewer_columns(self, mocker):
        tsv = "go_p\nprocess only\n"
        resp = httpx.Response(200, text=tsv)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=resp)
        mocker.patch(
            "pipeline.uniprot_fetch._client_manager.get",
            return_value=mock_client,
        )

        result = await fetch_uniprot_go_info("P12345")
        assert result["biological_process"] == "process only"
        assert result["molecular_function"] is None
        assert result["cellular_component"] is None

    async def test_timeout_returns_all_none(self, mocker):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        mocker.patch(
            "pipeline.uniprot_fetch._client_manager.get",
            return_value=mock_client,
        )

        result = await fetch_uniprot_go_info("P12345")
        assert all(v is None for v in result.values())


# ---------------------------------------------------------------------------
# fetch_uniprot_info (cached wrapper)
# ---------------------------------------------------------------------------


class TestFetchUniProtInfo:
    async def test_cache_hit(self):
        import pipeline.uniprot_fetch as mod

        cached = UniProtInfo("NOTCH3", "P12345", "Notch 3", None, None, None, "url")
        mod._uniprot_cache["NOTCH3"] = cached

        result = await fetch_uniprot_info("NOTCH3")
        assert result is cached

    async def test_cache_miss_fetches_and_caches(self, mocker):
        mocker.patch(
            "pipeline.uniprot_fetch.fetch_uniprot_accession",
            return_value=("P12345", "Notch 3"),
        )
        mocker.patch(
            "pipeline.uniprot_fetch.fetch_uniprot_go_info",
            return_value={
                "biological_process": "bp",
                "molecular_function": "mf",
                "cellular_component": "cc",
            },
        )

        result = await fetch_uniprot_info("NOTCH3")
        assert result.accession == "P12345"
        assert result.url == "https://www.uniprot.org/uniprotkb/P12345/entry"

        import pipeline.uniprot_fetch as mod

        assert "NOTCH3" in mod._uniprot_cache

    async def test_no_accession_returns_empty_info(self, mocker):
        mocker.patch(
            "pipeline.uniprot_fetch.fetch_uniprot_accession",
            return_value=(None, None),
        )

        result = await fetch_uniprot_info("FAKE")
        assert result.accession is None
        assert result.url is None
        assert result.protein_name is None


# ---------------------------------------------------------------------------
# fetch_uniprot_batch
# ---------------------------------------------------------------------------


class TestFetchUniProtBatch:
    async def test_empty_list(self):
        result = await fetch_uniprot_batch([])
        assert result == []

    async def test_progress_callback(self, mocker):
        info = UniProtInfo("A", "P1", None, None, None, None, None)
        mocker.patch(
            "pipeline.uniprot_fetch.fetch_uniprot_info",
            return_value=info,
        )
        calls = []
        await fetch_uniprot_batch(
            ["A", "B"],
            progress_callback=lambda cur, tot: calls.append((cur, tot)),
        )
        assert len(calls) == 2
        assert calls[-1] == (2, 2)


# ---------------------------------------------------------------------------
# sync_uniprot_info
# ---------------------------------------------------------------------------


class TestSyncUniProtInfo:
    async def test_all_cached(self, mocker):
        mocker.patch(
            "pipeline.database.get_cached_uniprot_info",
            return_value={"NOTCH3": {}, "HTRA1": {}},
        )

        result = await sync_uniprot_info(["NOTCH3", "HTRA1"])
        assert isinstance(result, SyncResult)
        assert result.fetched == 0
        assert result.cached == 2

    async def test_fetches_missing_stores_results(self, mocker):
        mocker.patch(
            "pipeline.database.get_cached_uniprot_info",
            return_value={"NOTCH3": {}},
        )
        successful = UniProtInfo("HTRA1", "P99999", "prot", None, None, None, "url")
        failed = UniProtInfo("FAKE", None, None, None, None, None, None)
        mocker.patch(
            "pipeline.uniprot_fetch.fetch_uniprot_batch",
            return_value=[successful, failed],
        )
        mock_upsert = mocker.patch(
            "pipeline.database.upsert_uniprot_batch",
            return_value=1,
        )

        result = await sync_uniprot_info(["NOTCH3", "HTRA1", "FAKE"])
        assert result.cached == 1
        assert result.fetched == 1
        assert result.failed == 1
        assert any("FAKE" in e for e in result.errors)
        # Both successful and failed are upserted
        assert mock_upsert.call_count == 2
