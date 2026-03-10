"""Tests for pipeline.llm_extraction — GeneEntry model, parsing, extraction."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import anthropic
import httpx
import pytest
from pydantic import ValidationError

from pipeline.llm_extraction import (
    ExtractionResult,
    GeneEntry,
    _parse_extraction_response,
    extract_from_paper,
)

# ---------------------------------------------------------------------------
# GeneEntry Pydantic model
# ---------------------------------------------------------------------------


class TestGeneEntryModel:
    def test_minimal_valid(self):
        ge = GeneEntry(gene_symbol="NOTCH3", confidence=0.9)
        assert ge.gene_symbol == "NOTCH3"
        assert ge.confidence == 0.9
        assert ge.protein_name is None
        assert ge.gwas_trait == []
        assert ge.omics_evidence == []
        assert ge.mendelian_randomization is False
        assert ge.pmid == ""

    def test_full_fields(self):
        ge = GeneEntry(
            gene_symbol="HTRA1",
            protein_name="Serine protease HTRA1",
            gwas_trait=["WMH"],
            mendelian_randomization=True,
            omics_evidence=["TWAS"],
            confidence=0.95,
            causal_evidence_summary="Strong evidence",
            pmid="12345678",
        )
        assert ge.gene_symbol == "HTRA1"
        assert ge.mendelian_randomization is True

    def test_confidence_lower_bound(self):
        ge = GeneEntry(gene_symbol="X", confidence=0.0)
        assert ge.confidence == 0.0

    def test_confidence_upper_bound(self):
        ge = GeneEntry(gene_symbol="X", confidence=1.0)
        assert ge.confidence == 1.0

    def test_confidence_below_zero_rejected(self):
        with pytest.raises(ValidationError):
            GeneEntry(gene_symbol="X", confidence=-0.1)

    def test_confidence_above_one_rejected(self):
        with pytest.raises(ValidationError):
            GeneEntry(gene_symbol="X", confidence=1.1)

    def test_whitespace_stripped(self):
        ge = GeneEntry(gene_symbol="  NOTCH3  ", confidence=0.9)
        assert ge.gene_symbol == "NOTCH3"

    def test_missing_gene_symbol_rejected(self):
        with pytest.raises(ValidationError):
            GeneEntry(confidence=0.9)  # type: ignore[call-arg]

    def test_missing_confidence_rejected(self):
        with pytest.raises(ValidationError):
            GeneEntry(gene_symbol="X")  # type: ignore[call-arg]

    def test_pmid_mutable(self):
        ge = GeneEntry(gene_symbol="X", confidence=0.9)
        ge.pmid = "99999999"
        assert ge.pmid == "99999999"


class TestExtractionResult:
    def test_empty_genes(self):
        er = ExtractionResult(genes=[])
        assert er.genes == []

    def test_default_empty(self):
        er = ExtractionResult()
        assert er.genes == []

    def test_with_genes(self):
        er = ExtractionResult(
            genes=[GeneEntry(gene_symbol="X", confidence=0.9)]
        )
        assert len(er.genes) == 1

    def test_model_json_schema(self):
        schema = ExtractionResult.model_json_schema()
        assert "properties" in schema
        assert "genes" in schema["properties"]


# ---------------------------------------------------------------------------
# _parse_extraction_response
# ---------------------------------------------------------------------------


class TestParseExtractionResponse:
    def test_clean_json(self):
        text = '{"genes": [{"gene_symbol": "NOTCH3", "confidence": 0.9}]}'
        result = _parse_extraction_response(text)
        assert len(result.genes) == 1
        assert result.genes[0].gene_symbol == "NOTCH3"

    def test_empty_genes(self):
        result = _parse_extraction_response('{"genes": []}')
        assert result.genes == []

    def test_no_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_extraction_response("no json here at all")

    def test_invalid_schema_returns_empty(self):
        # ExtractionResult has a default empty genes list, so unknown keys
        # don't raise — they just produce an empty result.
        result = _parse_extraction_response('{"not_genes": []}')
        assert result.genes == []

    def test_whitespace_handling(self):
        text = '  \n  {"genes": []}  \n  '
        result = _parse_extraction_response(text)
        assert result.genes == []

    def test_multiple_genes(self):
        data = {
            "genes": [
                {"gene_symbol": "A", "confidence": 0.9},
                {"gene_symbol": "B", "confidence": 0.8},
                {"gene_symbol": "C", "confidence": 0.7},
            ]
        }
        result = _parse_extraction_response(json.dumps(data))
        assert len(result.genes) == 3

    def test_confidence_out_of_range_raises(self):
        text = '{"genes": [{"gene_symbol": "X", "confidence": 1.5}]}'
        with pytest.raises(ValidationError):
            _parse_extraction_response(text)


# ---------------------------------------------------------------------------
# extract_from_paper (async, mocked client)
# ---------------------------------------------------------------------------


class TestExtractFromPaper:
    async def test_empty_text_returns_empty(self):
        genes, usage = await extract_from_paper("", "12345678")
        assert genes == []
        assert usage.total_tokens == 0

    async def test_whitespace_text_returns_empty(self):
        genes, usage = await extract_from_paper("   \n  ", "12345678")
        assert genes == []

    @staticmethod
    def _make_mock_client(response):
        """Create a mock Anthropic client with properly mocked streaming.

        client.messages.stream(**kwargs) is a sync call that returns an
        async context manager. We use MagicMock for the sync parts and
        AsyncMock for the async parts.
        """
        stream_obj = AsyncMock()
        stream_obj.get_final_message = AsyncMock(return_value=response)

        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=stream_obj)
        cm.__aexit__ = AsyncMock(return_value=False)

        mock_client = MagicMock()
        mock_client.messages.stream.return_value = cm
        return mock_client

    async def test_successful_extraction(self, mocker, mock_anthropic_response):
        response_json = json.dumps(
            {
                "genes": [
                    {
                        "gene_symbol": "NOTCH3",
                        "confidence": 0.9,
                        "protein_name": "Notch 3",
                    }
                ]
            }
        )
        response = mock_anthropic_response(
            text=response_json, input_tokens=500, output_tokens=200
        )
        mock_client = self._make_mock_client(response)
        mocker.patch(
            "pipeline.llm_extraction._get_async_client",
            return_value=mock_client,
        )

        genes, usage = await extract_from_paper(
            "This paper discusses NOTCH3...", "12345678"
        )
        assert len(genes) == 1
        assert genes[0].gene_symbol == "NOTCH3"
        assert usage.input_tokens == 500
        assert usage.output_tokens == 200

    async def test_empty_response_text(self, mocker, mock_anthropic_response):
        response = mock_anthropic_response(text="   ")
        mock_client = self._make_mock_client(response)
        mocker.patch(
            "pipeline.llm_extraction._get_async_client",
            return_value=mock_client,
        )

        genes, usage = await extract_from_paper("Some paper text", "12345678")
        assert genes == []

    async def test_thinking_blocks_skipped(self, mocker, mock_anthropic_response):
        response = mock_anthropic_response(
            text='{"genes": []}', include_thinking=True
        )
        mock_client = self._make_mock_client(response)
        mocker.patch(
            "pipeline.llm_extraction._get_async_client",
            return_value=mock_client,
        )

        genes, _ = await extract_from_paper("Paper text", "12345678")
        assert genes == []

    async def test_api_error_returns_empty(self, mocker):
        mock_client = MagicMock()
        mock_client.messages.stream.side_effect = anthropic.APIError(
            message="Internal server error",
            request=MagicMock(),
            body=None,
        )
        mocker.patch(
            "pipeline.llm_extraction._get_async_client",
            return_value=mock_client,
        )

        genes, usage = await extract_from_paper("Paper text", "12345678")
        assert genes == []

    async def test_rate_limiter_called(
        self, mocker, mock_anthropic_response, config
    ):
        response = mock_anthropic_response(text='{"genes": []}')
        mock_client = self._make_mock_client(response)
        mocker.patch(
            "pipeline.llm_extraction._get_async_client",
            return_value=mock_client,
        )

        rate_limiter = AsyncMock()
        rate_limiter.acquire = AsyncMock(return_value=0)
        rate_limiter.record_actual_usage = AsyncMock()

        await extract_from_paper(
            "Paper text",
            "12345678",
            config=config,
            rate_limiter=rate_limiter,
        )
        rate_limiter.acquire.assert_awaited_once()

    async def test_rate_limiter_zeroed_on_rate_limit_error(
        self, mocker, mock_anthropic_response
    ):
        """Bug 2: rate limiter reservation released on 429 error."""
        # First call raises RateLimitError, second succeeds
        good_response = mock_anthropic_response(text='{"genes": []}')

        good_stream = AsyncMock()
        good_stream.get_final_message = AsyncMock(return_value=good_response)
        good_cm = MagicMock()
        good_cm.__aenter__ = AsyncMock(return_value=good_stream)
        good_cm.__aexit__ = AsyncMock(return_value=False)

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.headers = {"retry-after": "0.01"}
        mock_client.messages.stream.side_effect = [
            anthropic.RateLimitError(
                message="rate limited",
                response=mock_response,
                body=None,
            ),
            good_cm,
        ]
        mocker.patch(
            "pipeline.llm_extraction._get_async_client",
            return_value=mock_client,
        )
        mocker.patch("asyncio.sleep", new_callable=AsyncMock)

        rate_limiter = AsyncMock()
        # Return different request IDs for each acquire call
        rate_limiter.acquire = AsyncMock(side_effect=[0, 1])
        rate_limiter.record_actual_usage = AsyncMock()

        from pipeline.config import PipelineConfig

        cfg = PipelineConfig(max_rate_limit_retries=3)
        await extract_from_paper(
            "Paper text", "12345678", config=cfg, rate_limiter=rate_limiter
        )

        # First call should zero out reservation (request_id=0, actual=0)
        first_call = rate_limiter.record_actual_usage.call_args_list[0]
        assert first_call.args == (0, 0)

    async def test_rate_limiter_zeroed_on_connection_error(
        self, mocker, mock_anthropic_response
    ):
        """Bug 2: rate limiter reservation released on connection error."""
        good_response = mock_anthropic_response(text='{"genes": []}')

        good_stream = AsyncMock()
        good_stream.get_final_message = AsyncMock(return_value=good_response)
        good_cm = MagicMock()
        good_cm.__aenter__ = AsyncMock(return_value=good_stream)
        good_cm.__aexit__ = AsyncMock(return_value=False)

        mock_client = MagicMock()
        mock_client.messages.stream.side_effect = [
            httpx.RemoteProtocolError("connection lost"),
            good_cm,
        ]
        mocker.patch(
            "pipeline.llm_extraction._get_async_client",
            return_value=mock_client,
        )
        mocker.patch("asyncio.sleep", new_callable=AsyncMock)

        rate_limiter = AsyncMock()
        rate_limiter.acquire = AsyncMock(side_effect=[0, 1])
        rate_limiter.record_actual_usage = AsyncMock()

        from pipeline.config import PipelineConfig

        cfg = PipelineConfig(max_connection_retries=3)
        await extract_from_paper(
            "Paper text", "12345678", config=cfg, rate_limiter=rate_limiter
        )

        # First call should zero out reservation (request_id=0, actual=0)
        first_call = rate_limiter.record_actual_usage.call_args_list[0]
        assert first_call.args == (0, 0)

    async def test_validation_retry_on_bad_confidence(
        self, mocker, mock_anthropic_response
    ):
        """First response has out-of-range confidence, second is valid — should retry."""
        bad_response = mock_anthropic_response(
            text='{"genes": [{"gene_symbol": "X", "confidence": 1.5}]}'
        )
        good_response = mock_anthropic_response(text='{"genes": []}')

        bad_stream = AsyncMock()
        bad_stream.get_final_message = AsyncMock(return_value=bad_response)
        bad_cm = MagicMock()
        bad_cm.__aenter__ = AsyncMock(return_value=bad_stream)
        bad_cm.__aexit__ = AsyncMock(return_value=False)

        good_stream = AsyncMock()
        good_stream.get_final_message = AsyncMock(return_value=good_response)
        good_cm = MagicMock()
        good_cm.__aenter__ = AsyncMock(return_value=good_stream)
        good_cm.__aexit__ = AsyncMock(return_value=False)

        mock_client = MagicMock()
        mock_client.messages.stream.side_effect = [bad_cm, good_cm]
        mocker.patch(
            "pipeline.llm_extraction._get_async_client",
            return_value=mock_client,
        )

        from pipeline.config import PipelineConfig

        cfg = PipelineConfig(max_retries=2)
        genes, _ = await extract_from_paper("Paper text", "12345678", config=cfg)
        assert genes == []  # Second call should succeed with empty genes
        assert mock_client.messages.stream.call_count == 2

    async def test_connection_error_retries_then_succeeds(
        self, mocker, mock_anthropic_response
    ):
        """Connection error on first call, success on second → 2 total calls."""
        response = mock_anthropic_response(text='{"genes": []}')

        good_stream = AsyncMock()
        good_stream.get_final_message = AsyncMock(return_value=response)
        good_cm = MagicMock()
        good_cm.__aenter__ = AsyncMock(return_value=good_stream)
        good_cm.__aexit__ = AsyncMock(return_value=False)

        mock_client = MagicMock()
        mock_client.messages.stream.side_effect = [
            httpx.RemoteProtocolError(
                "peer closed connection without sending complete message body"
            ),
            good_cm,
        ]
        mocker.patch(
            "pipeline.llm_extraction._get_async_client",
            return_value=mock_client,
        )
        mocker.patch("asyncio.sleep", new_callable=AsyncMock)

        from pipeline.config import PipelineConfig

        cfg = PipelineConfig(max_connection_retries=3)
        genes, _ = await extract_from_paper("Paper text", "12345678", config=cfg)
        assert genes == []  # empty genes from good response
        assert mock_client.messages.stream.call_count == 2

    async def test_connection_error_retries_exhausted(self, mocker):
        """Persistent connection error exhausts retries → returns empty."""
        mock_client = MagicMock()
        mock_client.messages.stream.side_effect = httpx.RemoteProtocolError(
            "peer closed connection without sending complete message body"
        )
        mocker.patch(
            "pipeline.llm_extraction._get_async_client",
            return_value=mock_client,
        )
        mocker.patch("asyncio.sleep", new_callable=AsyncMock)

        from pipeline.config import PipelineConfig

        cfg = PipelineConfig(max_connection_retries=3)
        genes, _ = await extract_from_paper("Paper text", "12345678", config=cfg)
        assert genes == []
        assert mock_client.messages.stream.call_count == cfg.max_connection_retries + 1
