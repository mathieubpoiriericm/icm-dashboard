"""Tests for pipeline.external_data_sync — external data sync orchestrator."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from pipeline.external_data_sync import (
    ExternalDataSyncResult,
    get_all_pmids,
    get_table1_gene_symbols,
    get_table2_gene_symbols,
    sync_all_external_data,
)
from pipeline.ncbi_gene_fetch import SyncResult as NCBISyncResult
from pipeline.pubmed_citations import SyncResult as PubMedSyncResult
from pipeline.uniprot_fetch import SyncResult as UniProtSyncResult

# ---------------------------------------------------------------------------
# ExternalDataSyncResult
# ---------------------------------------------------------------------------


class TestExternalDataSyncResult:
    def test_default_values(self):
        r = ExternalDataSyncResult()
        assert r.ncbi_fetched == 0
        assert r.ncbi_cached == 0
        assert r.ncbi_failed == 0
        assert r.uniprot_fetched == 0
        assert r.uniprot_cached == 0
        assert r.uniprot_failed == 0
        assert r.pubmed_fetched == 0
        assert r.pubmed_cached == 0
        assert r.pubmed_failed == 0
        assert r.errors == []

    def test_summary_format(self):
        r = ExternalDataSyncResult(
            ncbi_fetched=5,
            ncbi_cached=10,
            ncbi_failed=1,
            uniprot_fetched=3,
            uniprot_cached=8,
            uniprot_failed=2,
            pubmed_fetched=20,
            pubmed_cached=50,
            pubmed_failed=0,
        )
        s = r.summary()
        assert "NCBI: 5 fetched" in s
        assert "UniProt: 3 fetched" in s
        assert "PubMed: 20 fetched" in s


# ---------------------------------------------------------------------------
# get_table1_gene_symbols
# ---------------------------------------------------------------------------


class TestGetTable1GeneSymbols:
    async def test_returns_gene_list(self, mocker):
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(
            return_value=[{"gene": "NOTCH3"}, {"gene": "HTRA1"}]
        )
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mocker.patch(
            "pipeline.external_data_sync.Database.connection",
            return_value=mock_ctx,
        )

        result = await get_table1_gene_symbols()
        assert result == ["NOTCH3", "HTRA1"]


# ---------------------------------------------------------------------------
# get_table2_gene_symbols
# ---------------------------------------------------------------------------


class TestGetTable2GeneSymbols:
    async def test_parses_comma_separated(self, mocker):
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(
            return_value=[{"genetic_target": "NOTCH3, HTRA1"}]
        )
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mocker.patch(
            "pipeline.external_data_sync.Database.connection",
            return_value=mock_ctx,
        )

        result = await get_table2_gene_symbols()
        assert "NOTCH3" in result
        assert "HTRA1" in result

    async def test_filters_na_and_dash(self, mocker):
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(
            return_value=[
                {"genetic_target": "NOTCH3, NA, -, HTRA1"},
            ]
        )
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mocker.patch(
            "pipeline.external_data_sync.Database.connection",
            return_value=mock_ctx,
        )

        result = await get_table2_gene_symbols()
        assert "NA" not in result
        assert "-" not in result
        assert "NOTCH3" in result

    async def test_handles_none_target(self, mocker):
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(
            return_value=[{"genetic_target": None}]
        )
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mocker.patch(
            "pipeline.external_data_sync.Database.connection",
            return_value=mock_ctx,
        )

        result = await get_table2_gene_symbols()
        assert result == []

    async def test_returns_sorted_deduplicated(self, mocker):
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(
            return_value=[
                {"genetic_target": "HTRA1, NOTCH3"},
                {"genetic_target": "NOTCH3"},
            ]
        )
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mocker.patch(
            "pipeline.external_data_sync.Database.connection",
            return_value=mock_ctx,
        )

        result = await get_table2_gene_symbols()
        assert result == sorted(set(result))


# ---------------------------------------------------------------------------
# get_all_pmids
# ---------------------------------------------------------------------------


class TestGetAllPmids:
    async def test_extracts_pmids(self, mocker):
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(
            return_value=[
                {"references": "12345678, 23456789"},
                {"references": "34567890"},
            ]
        )
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mocker.patch(
            "pipeline.external_data_sync.Database.connection",
            return_value=mock_ctx,
        )

        result = await get_all_pmids()
        assert "12345678" in result
        assert "23456789" in result
        assert "34567890" in result


# ---------------------------------------------------------------------------
# sync_all_external_data
# ---------------------------------------------------------------------------


def _mock_cleanup(mocker):
    """Mock all close/clear cleanup calls used in the finally block."""
    _mod = "pipeline.external_data_sync"
    mocker.patch(f"{_mod}.close_ncbi_client", new_callable=AsyncMock)
    mocker.patch(
        f"{_mod}.close_uniprot_client", new_callable=AsyncMock
    )
    mocker.patch(
        f"{_mod}.close_pubmed_client", new_callable=AsyncMock
    )
    mocker.patch(f"{_mod}.clear_ncbi_cache")
    mocker.patch(f"{_mod}.clear_uniprot_cache")
    mocker.patch(f"{_mod}.clear_pubmed_cache")


class TestSyncAllExternalData:
    async def test_orchestrates_all_syncs(self, mocker):
        mocker.patch(
            "pipeline.external_data_sync.get_table1_gene_symbols",
            return_value=["NOTCH3"],
        )
        mocker.patch(
            "pipeline.external_data_sync.get_table2_gene_symbols",
            return_value=["HTRA1"],
        )
        mocker.patch(
            "pipeline.external_data_sync.get_all_pmids",
            return_value=["12345678"],
        )
        mocker.patch(
            "pipeline.external_data_sync.sync_ncbi_gene_info",
            return_value=NCBISyncResult(
                fetched=2, cached=0, failed=0, errors=[]
            ),
        )
        mocker.patch(
            "pipeline.external_data_sync.sync_uniprot_info",
            return_value=UniProtSyncResult(
                fetched=1, cached=0, failed=0, errors=[]
            ),
        )
        mocker.patch(
            "pipeline.external_data_sync.sync_pubmed_citations",
            return_value=PubMedSyncResult(
                fetched=1, cached=0, failed=0, errors=[]
            ),
        )
        _mock_cleanup(mocker)

        result = await sync_all_external_data()
        assert result.ncbi_fetched == 2
        assert result.uniprot_fetched == 1
        assert result.pubmed_fetched == 1

    async def test_deduplicates_genes(self, mocker):
        mocker.patch(
            "pipeline.external_data_sync.get_table1_gene_symbols",
            return_value=["NOTCH3", "HTRA1"],
        )
        mocker.patch(
            "pipeline.external_data_sync.get_table2_gene_symbols",
            return_value=["NOTCH3"],  # Duplicate
        )
        mocker.patch(
            "pipeline.external_data_sync.get_all_pmids",
            return_value=[],
        )
        mock_ncbi = mocker.patch(
            "pipeline.external_data_sync.sync_ncbi_gene_info",
            return_value=NCBISyncResult(
                fetched=2, cached=0, failed=0, errors=[]
            ),
        )
        mocker.patch(
            "pipeline.external_data_sync.sync_uniprot_info",
            return_value=UniProtSyncResult(
                fetched=0, cached=0, failed=0, errors=[]
            ),
        )
        _mock_cleanup(mocker)

        await sync_all_external_data()
        # NCBI sync receives deduplicated: [NOTCH3, HTRA1]
        call_args = mock_ncbi.call_args[0][0]
        assert len(call_args) == 2

    async def test_skips_pubmed_when_no_pmids(self, mocker):
        mocker.patch(
            "pipeline.external_data_sync.get_table1_gene_symbols",
            return_value=["NOTCH3"],
        )
        mocker.patch(
            "pipeline.external_data_sync.get_table2_gene_symbols",
            return_value=[],
        )
        mocker.patch(
            "pipeline.external_data_sync.get_all_pmids",
            return_value=[],
        )
        mocker.patch(
            "pipeline.external_data_sync.sync_ncbi_gene_info",
            return_value=NCBISyncResult(
                fetched=0, cached=1, failed=0, errors=[]
            ),
        )
        mocker.patch(
            "pipeline.external_data_sync.sync_uniprot_info",
            return_value=UniProtSyncResult(
                fetched=0, cached=1, failed=0, errors=[]
            ),
        )
        mock_pubmed = mocker.patch(
            "pipeline.external_data_sync.sync_pubmed_citations",
        )
        _mock_cleanup(mocker)

        result = await sync_all_external_data()
        mock_pubmed.assert_not_called()
        assert result.pubmed_fetched == 0

    async def test_errors_limited_to_10(self, mocker):
        mocker.patch(
            "pipeline.external_data_sync.get_table1_gene_symbols",
            return_value=["A"],
        )
        mocker.patch(
            "pipeline.external_data_sync.get_table2_gene_symbols",
            return_value=[],
        )
        mocker.patch(
            "pipeline.external_data_sync.get_all_pmids",
            return_value=[],
        )
        mocker.patch(
            "pipeline.external_data_sync.sync_ncbi_gene_info",
            return_value=NCBISyncResult(
                fetched=0,
                cached=0,
                failed=15,
                errors=[f"err{i}" for i in range(15)],
            ),
        )
        mocker.patch(
            "pipeline.external_data_sync.sync_uniprot_info",
            return_value=UniProtSyncResult(
                fetched=0, cached=0, failed=0, errors=[]
            ),
        )
        _mock_cleanup(mocker)

        result = await sync_all_external_data()
        assert len(result.errors) == 10

    async def test_cleanup_called_on_exception(self, mocker):
        mocker.patch(
            "pipeline.external_data_sync.get_table1_gene_symbols",
            side_effect=RuntimeError("boom"),
        )
        _mod = "pipeline.external_data_sync"
        mock_close_ncbi = mocker.patch(
            f"{_mod}.close_ncbi_client",
            new_callable=AsyncMock,
        )
        mock_close_uniprot = mocker.patch(
            f"{_mod}.close_uniprot_client",
            new_callable=AsyncMock,
        )
        mock_close_pubmed = mocker.patch(
            f"{_mod}.close_pubmed_client",
            new_callable=AsyncMock,
        )
        mocker.patch(f"{_mod}.clear_ncbi_cache")
        mocker.patch(f"{_mod}.clear_uniprot_cache")
        mocker.patch(f"{_mod}.clear_pubmed_cache")

        with pytest.raises(RuntimeError, match="boom"):
            await sync_all_external_data()

        mock_close_ncbi.assert_called_once()
        mock_close_uniprot.assert_called_once()
        mock_close_pubmed.assert_called_once()
