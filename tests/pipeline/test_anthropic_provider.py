"""Tests for pipeline.llm_providers.anthropic_provider — streaming, thinking,
structured outputs, and retry logic."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import anthropic
import httpx

from pipeline.llm_providers.anthropic_provider import AnthropicProvider

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# AnthropicProvider.extract — Claude-specific behavior
# ---------------------------------------------------------------------------


class TestAnthropicProviderExtract:
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
        mock_client = _make_mock_client(response)

        provider = AnthropicProvider()
        mocker.patch.object(provider, "_get_client", return_value=mock_client)

        from pipeline.config import PipelineConfig
        genes, usage = await provider.extract(
            "This paper discusses NOTCH3...", "12345678", PipelineConfig(), None
        )
        assert len(genes) == 1
        assert genes[0].gene_symbol == "NOTCH3"
        assert usage.input_tokens == 500
        assert usage.output_tokens == 200

    async def test_empty_response_text(self, mocker, mock_anthropic_response):
        response = mock_anthropic_response(text="   ")
        mock_client = _make_mock_client(response)

        provider = AnthropicProvider()
        mocker.patch.object(provider, "_get_client", return_value=mock_client)

        from pipeline.config import PipelineConfig
        genes, usage = await provider.extract(
            "Some paper text", "12345678", PipelineConfig(), None
        )
        assert genes == []

    async def test_thinking_blocks_skipped(self, mocker, mock_anthropic_response):
        response = mock_anthropic_response(text='{"genes": []}', include_thinking=True)
        mock_client = _make_mock_client(response)

        provider = AnthropicProvider()
        mocker.patch.object(provider, "_get_client", return_value=mock_client)

        from pipeline.config import PipelineConfig
        genes, _ = await provider.extract(
            "Paper text", "12345678", PipelineConfig(), None
        )
        assert genes == []

    async def test_api_error_returns_empty(self, mocker):
        mock_client = MagicMock()
        mock_client.messages.stream.side_effect = anthropic.APIError(
            message="Internal server error",
            request=MagicMock(),
            body=None,
        )

        provider = AnthropicProvider()
        mocker.patch.object(provider, "_get_client", return_value=mock_client)

        from pipeline.config import PipelineConfig
        genes, usage = await provider.extract(
            "Paper text", "12345678", PipelineConfig(), None
        )
        assert genes == []

    async def test_rate_limiter_called(self, mocker, mock_anthropic_response, config):
        response = mock_anthropic_response(text='{"genes": []}')
        mock_client = _make_mock_client(response)

        provider = AnthropicProvider()
        mocker.patch.object(provider, "_get_client", return_value=mock_client)

        rate_limiter = AsyncMock()
        rate_limiter.acquire = AsyncMock(return_value=0)
        rate_limiter.record_actual_usage = AsyncMock()

        await provider.extract(
            "Paper text",
            "12345678",
            config,
            rate_limiter,
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

        provider = AnthropicProvider()
        mocker.patch.object(provider, "_get_client", return_value=mock_client)
        mocker.patch("asyncio.sleep", new_callable=AsyncMock)

        rate_limiter = MagicMock()
        # Return different request IDs for each acquire call
        rate_limiter.acquire = AsyncMock(side_effect=[0, 1])
        rate_limiter.record_actual_usage = AsyncMock()
        rate_limiter.signal_rate_limit = AsyncMock()

        from pipeline.config import PipelineConfig

        cfg = PipelineConfig(max_rate_limit_retries=3)
        await provider.extract("Paper text", "12345678", cfg, rate_limiter)

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

        provider = AnthropicProvider()
        mocker.patch.object(provider, "_get_client", return_value=mock_client)
        mocker.patch("asyncio.sleep", new_callable=AsyncMock)

        rate_limiter = AsyncMock()
        rate_limiter.acquire = AsyncMock(side_effect=[0, 1])
        rate_limiter.record_actual_usage = AsyncMock()

        from pipeline.config import PipelineConfig

        cfg = PipelineConfig(max_connection_retries=3)
        await provider.extract("Paper text", "12345678", cfg, rate_limiter)

        # First call should zero out reservation (request_id=0, actual=0)
        first_call = rate_limiter.record_actual_usage.call_args_list[0]
        assert first_call.args == (0, 0)

    async def test_validation_retry_on_bad_confidence(
        self, mocker, mock_anthropic_response
    ):
        """Out-of-range confidence in first response triggers retry; 2nd passes."""
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

        provider = AnthropicProvider()
        mocker.patch.object(provider, "_get_client", return_value=mock_client)

        from pipeline.config import PipelineConfig

        cfg = PipelineConfig(max_retries=2)
        genes, _ = await provider.extract("Paper text", "12345678", cfg, None)
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

        provider = AnthropicProvider()
        mocker.patch.object(provider, "_get_client", return_value=mock_client)
        mocker.patch("asyncio.sleep", new_callable=AsyncMock)

        from pipeline.config import PipelineConfig

        cfg = PipelineConfig(max_connection_retries=3)
        genes, _ = await provider.extract("Paper text", "12345678", cfg, None)
        assert genes == []  # empty genes from good response
        assert mock_client.messages.stream.call_count == 2

    async def test_connection_error_retries_exhausted(self, mocker):
        """Persistent connection error exhausts retries → returns empty."""
        mock_client = MagicMock()
        mock_client.messages.stream.side_effect = httpx.RemoteProtocolError(
            "peer closed connection without sending complete message body"
        )

        provider = AnthropicProvider()
        mocker.patch.object(provider, "_get_client", return_value=mock_client)
        mocker.patch("asyncio.sleep", new_callable=AsyncMock)

        from pipeline.config import PipelineConfig

        cfg = PipelineConfig(max_connection_retries=3)
        genes, _ = await provider.extract("Paper text", "12345678", cfg, None)
        assert genes == []
        assert mock_client.messages.stream.call_count == cfg.max_connection_retries + 1


# ---------------------------------------------------------------------------
# AnthropicProvider lifecycle
# ---------------------------------------------------------------------------


class TestAnthropicProviderLifecycle:
    def test_name(self):
        provider = AnthropicProvider()
        assert provider.name == "anthropic"

    def test_supports_thinking(self):
        provider = AnthropicProvider()
        assert provider.supports_thinking() is True

    def test_supports_prompt_caching(self):
        provider = AnthropicProvider()
        assert provider.supports_prompt_caching() is True

    def test_client_lazy_created(self, mocker):
        mock_cls = mocker.patch(
            "pipeline.llm_providers.anthropic_provider.anthropic.AsyncAnthropic"
        )
        provider = AnthropicProvider()
        assert provider._client is None
        provider._get_client()
        mock_cls.assert_called_once()
        assert provider._client is not None

    async def test_close_clears_client(self, mocker):
        mock_cls = mocker.patch(
            "pipeline.llm_providers.anthropic_provider.anthropic.AsyncAnthropic"
        )
        mock_instance = AsyncMock()
        mock_cls.return_value = mock_instance

        provider = AnthropicProvider()
        provider._get_client()
        await provider.close()

        mock_instance.close.assert_awaited_once()
        assert provider._client is None

    async def test_close_idempotent(self):
        provider = AnthropicProvider()
        # No client created — should not raise
        await provider.close()
        await provider.close()
