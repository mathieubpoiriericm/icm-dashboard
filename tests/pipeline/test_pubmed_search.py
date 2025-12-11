"""Tests for pipeline.pubmed_search — query building, filtering, search."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from pipeline.pubmed_search import (
    DISEASE_TERMS,
    GENETIC_TERMS,
    MARKER_TERMS,
    SVD_QUERY,
    PubMedSearchError,
    _build_query,
    filter_new_pmids,
    search_recent_papers,
)

# ---------------------------------------------------------------------------
# SVD_QUERY structure
# ---------------------------------------------------------------------------


class TestQueryStructure:
    def test_query_contains_disease_terms(self):
        for term in DISEASE_TERMS:
            assert term in SVD_QUERY

    def test_query_contains_genetic_terms(self):
        for term in GENETIC_TERMS:
            assert term in SVD_QUERY

    def test_query_contains_marker_terms(self):
        for term in MARKER_TERMS:
            assert term in SVD_QUERY

    def test_query_uses_title_abstract(self):
        assert "[Title/Abstract]" in SVD_QUERY

    def test_query_uses_boolean_operators(self):
        assert " AND " in SVD_QUERY
        assert " OR " in SVD_QUERY

    def test_build_query_returns_string(self):
        query = _build_query()
        assert isinstance(query, str)
        assert len(query) > 100

    def test_query_is_deterministic(self):
        assert _build_query() == _build_query()


# ---------------------------------------------------------------------------
# filter_new_pmids
# ---------------------------------------------------------------------------


class TestFilterNewPmids:
    def test_empty_inputs(self):
        assert filter_new_pmids([], set()) == []

    def test_all_new(self):
        result = filter_new_pmids(["1", "2", "3"], set())
        assert result == ["1", "2", "3"]

    def test_all_existing(self):
        result = filter_new_pmids(["1", "2"], {"1", "2"})
        assert result == []

    def test_mixed(self):
        result = filter_new_pmids(["1", "2", "3"], {"2"})
        assert result == ["1", "3"]

    def test_preserves_order(self):
        result = filter_new_pmids(["3", "1", "2"], {"2"})
        assert result == ["3", "1"]

    def test_deduplicates(self):
        result = filter_new_pmids(["1", "1", "2", "2"], set())
        assert result == ["1", "2"]

    def test_dedup_and_filter(self):
        result = filter_new_pmids(["1", "2", "1", "3"], {"2"})
        assert result == ["1", "3"]


# ---------------------------------------------------------------------------
# search_recent_papers
# ---------------------------------------------------------------------------


class TestSearchRecentPapers:
    async def test_invalid_days_back_zero(self):
        with pytest.raises(ValueError, match="days_back"):
            await search_recent_papers(0)

    async def test_invalid_days_back_negative(self):
        with pytest.raises(ValueError, match="days_back"):
            await search_recent_papers(-1)

    async def test_invalid_days_back_too_high(self):
        with pytest.raises(ValueError, match="days_back"):
            await search_recent_papers(365 * 10 + 1)

    async def test_valid_days_back_boundary_low(self, mocker):
        mock_handle = MagicMock()
        mocker.patch("pipeline.pubmed_search.Entrez.esearch", return_value=mock_handle)
        mocker.patch(
            "pipeline.pubmed_search.Entrez.read",
            return_value={"IdList": [], "Count": "0"},
        )
        result = await search_recent_papers(1)
        assert result == []

    async def test_valid_days_back_boundary_high(self, mocker):
        mock_handle = MagicMock()
        mocker.patch("pipeline.pubmed_search.Entrez.esearch", return_value=mock_handle)
        mocker.patch(
            "pipeline.pubmed_search.Entrez.read",
            return_value={"IdList": [], "Count": "0"},
        )
        result = await search_recent_papers(365 * 10)
        assert result == []

    async def test_returns_pmid_list(self, mocker):
        mock_handle = MagicMock()
        mocker.patch("pipeline.pubmed_search.Entrez.esearch", return_value=mock_handle)
        mocker.patch(
            "pipeline.pubmed_search.Entrez.read",
            return_value={"IdList": ["111", "222", "333"], "Count": "3"},
        )
        result = await search_recent_papers(7)
        assert result == ["111", "222", "333"]

    async def test_entrez_error_raises(self, mocker):
        from urllib.error import URLError

        mocker.patch(
            "pipeline.pubmed_search.Entrez.esearch",
            side_effect=URLError("Network error"),
        )
        with pytest.raises(PubMedSearchError, match="Entrez API"):
            await search_recent_papers(7)

    async def test_missing_id_list_key(self, mocker):
        mock_handle = MagicMock()
        mocker.patch("pipeline.pubmed_search.Entrez.esearch", return_value=mock_handle)
        mocker.patch(
            "pipeline.pubmed_search.Entrez.read",
            return_value={"Count": "0"},
        )
        result = await search_recent_papers(7)
        assert result == []

    async def test_pagination_fetches_all_results(self, mocker):
        """When total > retmax, pagination fetches remaining results."""
        mock_handle = MagicMock()
        mocker.patch("pipeline.pubmed_search.Entrez.esearch", return_value=mock_handle)
        # First call returns initial batch with WebEnv/QueryKey
        first_batch = {
            "IdList": [str(i) for i in range(500)],
            "Count": "700",
            "WebEnv": "WEBENV123",
            "QueryKey": "1",
        }
        # Pagination call returns remaining
        second_batch = {
            "IdList": [str(i) for i in range(500, 700)],
            "Count": "700",
        }
        mocker.patch(
            "pipeline.pubmed_search.Entrez.read",
            side_effect=[first_batch, second_batch],
        )
        result = await search_recent_papers(7)
        assert len(result) == 700
