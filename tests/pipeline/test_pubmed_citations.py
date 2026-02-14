"""Tests for pipeline.pubmed_citations — PubMed citation fetching and formatting."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx

from pipeline.pubmed_citations import (
    PubMedCitation,
    SyncResult,
    _fetch_pubmed_uncached,
    _format_authors,
    _format_citation,
    _parse_pubmed_xml,
    _title_case,
    clear_pubmed_cache,
    extract_pmids_from_text,
    fetch_pubmed_citation,
    fetch_pubmed_citations_batch,
    sync_pubmed_citations,
)

# ---------------------------------------------------------------------------
# extract_pmids_from_text
# ---------------------------------------------------------------------------


class TestExtractPmidsFromText:
    def test_empty_returns_empty(self):
        assert extract_pmids_from_text("") == []

    def test_none_returns_empty(self):
        assert extract_pmids_from_text(None) == []

    def test_single_8_digit(self):
        assert extract_pmids_from_text("12345678") == ["12345678"]

    def test_single_7_digit(self):
        assert extract_pmids_from_text("1234567") == ["1234567"]

    def test_multiple_comma_separated(self):
        result = extract_pmids_from_text("12345678, 23456789, 34567890")
        assert result == ["12345678", "23456789", "34567890"]

    def test_deduplicates(self):
        result = extract_pmids_from_text("12345678, 12345678, 23456789")
        assert result == ["12345678", "23456789"]

    def test_preserves_order(self):
        result = extract_pmids_from_text("99999999, 11111111, 55555555")
        assert result == ["99999999", "11111111", "55555555"]

    def test_pmid_prefix_format(self):
        result = extract_pmids_from_text("PMID: 12345678")
        assert result == ["12345678"]

    def test_ignores_6_digit(self):
        assert extract_pmids_from_text("123456") == []

    def test_ignores_9_digit(self):
        assert extract_pmids_from_text("123456789") == []


# ---------------------------------------------------------------------------
# _format_authors
# ---------------------------------------------------------------------------


class TestFormatAuthors:
    def test_empty_list(self):
        assert _format_authors([]) == ""

    def test_single_author(self):
        assert _format_authors(["Smith J"]) == "Smith J"

    def test_three_authors(self):
        result = _format_authors(["Smith J", "Doe A", "Lee B"])
        assert result == "Smith J, Doe A, Lee B"

    def test_four_or_more_authors(self):
        authors = ["Smith J", "Doe A", "Lee B", "Kim C"]
        result = _format_authors(authors)
        assert result == "Smith J, Doe A, Lee B, et al."

    def test_custom_max(self):
        authors = ["A", "B", "C"]
        result = _format_authors(authors, max_authors=2)
        assert result == "A, B, et al."


# ---------------------------------------------------------------------------
# _title_case
# ---------------------------------------------------------------------------


class TestTitleCase:
    def test_empty_string(self):
        assert _title_case("") == ""

    def test_capitalizes_first_word(self):
        result = _title_case("a study of genes")
        assert result[0] == "A"

    def test_preserves_rest(self):
        result = _title_case("a study")
        assert result == "A study"


# ---------------------------------------------------------------------------
# _format_citation
# ---------------------------------------------------------------------------


class TestFormatCitation:
    def test_all_fields_present(self):
        result = _format_citation("Smith J", "A study", "Nature", "2024", "10.1/x")
        assert "<b>Smith J</b>" in result
        assert "<i>" in result
        assert "Nature (2024)" in result
        assert "DOI: 10.1/x" in result

    def test_missing_fields(self):
        result = _format_citation(None, "A study", None, None, None)
        assert "<b>" not in result
        assert "<i>" in result

    def test_only_authors(self):
        result = _format_citation("Smith J", None, None, None, None)
        assert result == "<b>Smith J</b>"

    def test_journal_without_date(self):
        result = _format_citation(None, None, "Nature", None, None)
        assert result == "Nature"

    def test_journal_with_date(self):
        result = _format_citation(None, None, "Nature", "2024", None)
        assert "Nature (2024)" in result


# ---------------------------------------------------------------------------
# _parse_pubmed_xml
# ---------------------------------------------------------------------------

_FULL_ARTICLE_XML = b"""<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <Article>
        <ArticleTitle>Gene discovery in SVD</ArticleTitle>
        <Journal>
          <Title>Nature Genetics</Title>
        </Journal>
        <AuthorList>
          <Author><LastName>Smith</LastName><Initials>J</Initials></Author>
          <Author><LastName>Doe</LastName><Initials>A</Initials></Author>
        </AuthorList>
      </Article>
    </MedlineCitation>
    <PubmedData>
      <ArticleIdList>
        <ArticleId IdType="doi">10.1038/test</ArticleId>
        <ArticleId IdType="pubmed">12345678</ArticleId>
      </ArticleIdList>
      <History>
        <PubMedPubDate PubStatus="pubmed">
          <Year>2024</Year><Month>Jan</Month>
        </PubMedPubDate>
      </History>
    </PubmedData>
  </PubmedArticle>
</PubmedArticleSet>"""


class TestParsePubmedXml:
    def test_full_article(self):
        result = _parse_pubmed_xml("12345678", _FULL_ARTICLE_XML)
        assert result is not None
        assert result.pmid == "12345678"
        assert result.title == "Gene discovery in SVD"
        assert result.journal == "Nature Genetics"
        assert result.doi == "10.1038/test"
        assert "Smith" in result.authors

    def test_no_pubmed_article(self):
        xml = b"<PubmedArticleSet></PubmedArticleSet>"
        result = _parse_pubmed_xml("99999999", xml)
        assert result is not None
        assert result.title is None
        assert "citation not available" in result.formatted_ref

    def test_no_authors(self):
        xml = b"""<?xml version="1.0"?>
        <PubmedArticleSet>
          <PubmedArticle>
            <MedlineCitation>
              <Article>
                <ArticleTitle>Test</ArticleTitle>
                <Journal><Title>J Test</Title></Journal>
              </Article>
            </MedlineCitation>
            <PubmedData><ArticleIdList></ArticleIdList></PubmedData>
          </PubmedArticle>
        </PubmedArticleSet>"""
        result = _parse_pubmed_xml("11111111", xml)
        assert result is not None
        assert result.authors is None

    def test_no_doi(self):
        xml = b"""<?xml version="1.0"?>
        <PubmedArticleSet>
          <PubmedArticle>
            <MedlineCitation>
              <Article>
                <ArticleTitle>No DOI study</ArticleTitle>
                <Journal><Title>Some Journal</Title></Journal>
              </Article>
            </MedlineCitation>
            <PubmedData><ArticleIdList></ArticleIdList></PubmedData>
          </PubmedArticle>
        </PubmedArticleSet>"""
        result = _parse_pubmed_xml("22222222", xml)
        assert result is not None
        assert result.doi is None

    def test_xml_syntax_error(self):
        result = _parse_pubmed_xml("33333333", b"<not valid xml")
        assert result is None


# ---------------------------------------------------------------------------
# clear_pubmed_cache
# ---------------------------------------------------------------------------


class TestClearCache:
    def test_clears_cache(self):
        import pipeline.pubmed_citations as mod

        mod._citation_cache["12345678"] = PubMedCitation(
            "12345678", None, None, None, None, None, ""
        )
        assert "12345678" in mod._citation_cache
        clear_pubmed_cache()
        assert mod._citation_cache == {}


# ---------------------------------------------------------------------------
# fetch_pubmed_citation (cached wrapper)
# ---------------------------------------------------------------------------


class TestFetchPubmedCitation:
    async def test_cache_hit(self):
        import pipeline.pubmed_citations as mod

        cached = PubMedCitation("12345678", "auth", "title", "j", None, None, "ref")
        mod._citation_cache["12345678"] = cached

        result = await fetch_pubmed_citation("12345678")
        assert result is cached

    async def test_cache_miss_fetches(self, mocker):
        expected = PubMedCitation("12345678", "auth", "t", "j", None, None, "ref")
        mocker.patch(
            "pipeline.pubmed_citations._fetch_pubmed_uncached",
            return_value=expected,
        )
        result = await fetch_pubmed_citation("12345678")
        assert result is expected


# ---------------------------------------------------------------------------
# _fetch_pubmed_uncached
# ---------------------------------------------------------------------------


class TestFetchPubmedUncached:
    async def test_non_200_returns_none(self, mocker):
        resp = httpx.Response(500)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=resp)
        mocker.patch(
            "pipeline.pubmed_citations._client_manager.get",
            return_value=mock_client,
        )

        result = await _fetch_pubmed_uncached("12345678")
        assert result is None

    async def test_timeout_returns_none(self, mocker):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        mocker.patch(
            "pipeline.pubmed_citations._client_manager.get",
            return_value=mock_client,
        )

        result = await _fetch_pubmed_uncached("12345678")
        assert result is None

    async def test_request_error_returns_none(self, mocker):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.RequestError("connection reset"))
        mocker.patch(
            "pipeline.pubmed_citations._client_manager.get",
            return_value=mock_client,
        )

        result = await _fetch_pubmed_uncached("12345678")
        assert result is None

    async def test_successful_fetch_parses_xml(self, mocker):
        resp = httpx.Response(200, content=_FULL_ARTICLE_XML)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=resp)
        mocker.patch(
            "pipeline.pubmed_citations._client_manager.get",
            return_value=mock_client,
        )

        result = await _fetch_pubmed_uncached("12345678")
        assert result is not None
        assert result.pmid == "12345678"
        assert result.title == "Gene discovery in SVD"


# ---------------------------------------------------------------------------
# fetch_pubmed_citations_batch
# ---------------------------------------------------------------------------


class TestFetchPubmedCitationsBatch:
    async def test_placeholder_for_failed(self, mocker):
        mocker.patch(
            "pipeline.pubmed_citations.fetch_pubmed_citation",
            return_value=None,
        )
        result = await fetch_pubmed_citations_batch(["12345678"])
        assert len(result) == 1
        assert result[0].pmid == "12345678"
        assert "fetch failed" in result[0].formatted_ref

    async def test_progress_callback(self, mocker):
        citation = PubMedCitation("1", None, None, None, None, None, "")
        mocker.patch(
            "pipeline.pubmed_citations.fetch_pubmed_citation",
            return_value=citation,
        )
        calls = []
        await fetch_pubmed_citations_batch(
            ["1", "2"],
            progress_callback=lambda cur, tot: calls.append((cur, tot)),
        )
        assert len(calls) == 2
        assert calls[-1] == (2, 2)


# ---------------------------------------------------------------------------
# sync_pubmed_citations
# ---------------------------------------------------------------------------


class TestSyncPubmedCitations:
    async def test_all_cached(self, mocker):
        mocker.patch(
            "pipeline.database.get_cached_pubmed_citations",
            return_value={"12345678": {}, "23456789": {}},
        )

        result = await sync_pubmed_citations(["12345678", "23456789"])
        assert isinstance(result, SyncResult)
        assert result.fetched == 0
        assert result.cached == 2

    async def test_deduplicates_pmids(self, mocker):
        mocker.patch(
            "pipeline.database.get_cached_pubmed_citations",
            return_value={"12345678": {}},
        )

        # Even though "12345678" appears twice, it should be deduplicated
        result = await sync_pubmed_citations(["12345678", "12345678", "12345678"])
        assert result.cached == 1

    async def test_successful_vs_failed_counting(self, mocker):
        mocker.patch(
            "pipeline.database.get_cached_pubmed_citations",
            return_value={},
        )
        success = PubMedCitation("1", "auth", "A Title", "j", None, None, "ref")
        failed = PubMedCitation("2", None, None, None, None, None, "PMID: 2 (failed)")
        mocker.patch(
            "pipeline.pubmed_citations.fetch_pubmed_citations_batch",
            return_value=[success, failed],
        )
        mocker.patch(
            "pipeline.database.upsert_pubmed_citations_batch",
            return_value=2,
        )

        result = await sync_pubmed_citations(["1111111", "2222222"])
        assert result.fetched == 1  # title is not None
        assert result.failed == 1  # title is None
        assert any("PMID 2" in e for e in result.errors)
