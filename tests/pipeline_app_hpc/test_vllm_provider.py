"""Tests for pipeline_app_hpc.providers.vllm_provider."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest


class TestVllmProviderSurface:
    def test_name(self):
        from pipeline_app_hpc.providers.vllm_provider import VllmProvider

        p = VllmProvider(base_url="http://127.0.0.1:30800", model="svd")
        assert p.name == "vllm"

    def test_supports_thinking_false(self):
        from pipeline_app_hpc.providers.vllm_provider import VllmProvider

        p = VllmProvider(base_url="http://127.0.0.1:30800", model="svd")
        assert p.supports_thinking() is False

    def test_supports_prompt_caching_false(self):
        from pipeline_app_hpc.providers.vllm_provider import VllmProvider

        p = VllmProvider(base_url="http://127.0.0.1:30800", model="svd")
        assert p.supports_prompt_caching() is False

    @pytest.mark.asyncio
    async def test_close_idempotent(self):
        from pipeline_app_hpc.providers.vllm_provider import VllmProvider

        p = VllmProvider(base_url="http://127.0.0.1:30800", model="svd")
        await p.close()
        await p.close()  # second call must not raise

    def test_estimate_cost_returns_none(self):
        from pipeline_app_hpc.providers.vllm_provider import VllmProvider

        from pipeline.config import PipelineConfig
        from pipeline.quality_metrics import TokenUsage

        p = VllmProvider(base_url="http://127.0.0.1:30800", model="svd")
        assert p.estimate_cost(TokenUsage(), PipelineConfig()) is None

    def test_report_metadata_shape(self):
        from pipeline_app_hpc.providers.vllm_provider import VllmProvider

        from pipeline.config import PipelineConfig

        p = VllmProvider(
            base_url="http://127.0.0.1:30800",
            model="svd",
            base_model_name="unsloth/gemma-4-31b-it-unsloth-bnb-4bit",
            adapter_name="svd",
            max_model_len=16384,
            quantization="bitsandbytes",
        )
        meta = p.report_metadata(PipelineConfig())
        assert meta["model"] == "unsloth/gemma-4-31b-it-unsloth-bnb-4bit"
        assert meta["thinking_mode"] == "none"
        assert meta["effort"] is None
        assert meta["vllm_adapter"] == "svd"
        assert meta["vllm_max_model_len"] == 16384
        assert meta["vllm_quantization"] == "bitsandbytes"

    def test_report_metadata_no_adapter(self):
        from pipeline_app_hpc.providers.vllm_provider import VllmProvider

        from pipeline.config import PipelineConfig

        p = VllmProvider(
            base_url="http://127.0.0.1:30800",
            model="unsloth/gemma-4-31b-it-unsloth-bnb-4bit",
            base_model_name="unsloth/gemma-4-31b-it-unsloth-bnb-4bit",
            adapter_name="",
        )
        meta = p.report_metadata(PipelineConfig())
        assert meta["vllm_adapter"] is None


def _mock_chat_response(
    content: str,
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
    finish_reason: str = "stop",
) -> MagicMock:
    """Build a fake httpx.Response for /v1/chat/completions."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json = MagicMock(
        return_value={
            "choices": [
                {"message": {"content": content}, "finish_reason": finish_reason}
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            },
        }
    )
    resp.raise_for_status = MagicMock()
    return resp


class TestVllmProviderExtract:
    @pytest.mark.asyncio
    async def test_happy_path(self, mocker):
        from pipeline_app_hpc.providers.vllm_provider import VllmProvider

        from pipeline.config import PipelineConfig

        models_resp = MagicMock(status_code=200)
        models_resp.raise_for_status = MagicMock()

        chat_resp = _mock_chat_response(
            content=json.dumps(
                {
                    "genes": [
                        {
                            "gene_symbol": "NOTCH3",
                            "confidence": 0.92,
                            "gwas_trait": [],
                            "omics_evidence": [],
                            "mendelian_randomization": False,
                        }
                    ]
                }
            )
        )

        client_mock = AsyncMock(spec=httpx.AsyncClient)
        client_mock.get = AsyncMock(return_value=models_resp)
        client_mock.post = AsyncMock(return_value=chat_resp)
        client_mock.aclose = AsyncMock()

        mocker.patch(
            "pipeline_app_hpc.providers.vllm_provider.httpx.AsyncClient",
            return_value=client_mock,
        )

        p = VllmProvider(base_url="http://127.0.0.1:30800", model="svd")
        cfg = PipelineConfig()
        genes, usage = await p.extract("paper text", "PMID123", cfg, None)

        assert len(genes) == 1
        assert genes[0].gene_symbol == "NOTCH3"
        assert genes[0].pmid == "PMID123"
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50

        client_mock.post.assert_called_once()
        call = client_mock.post.call_args
        assert call[0][0] == "/v1/chat/completions"
        body = call[1]["json"]
        assert body["model"] == "svd"
        assert body["temperature"] == 0.0
        assert body["top_p"] == 1.0
        assert body["guided_decoding_backend"] == "outlines"
        assert "guided_json" in body
        assert body["messages"][0]["role"] == "system"
        assert body["messages"][1]["role"] == "user"

    @pytest.mark.asyncio
    async def test_ensure_healthy_raises_clear_error_on_unreachable(self, mocker):
        from pipeline_app_hpc.providers.vllm_provider import VllmProvider

        from pipeline.config import PipelineConfig

        client_mock = AsyncMock(spec=httpx.AsyncClient)
        client_mock.get = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        client_mock.aclose = AsyncMock()

        mocker.patch(
            "pipeline_app_hpc.providers.vllm_provider.httpx.AsyncClient",
            return_value=client_mock,
        )

        p = VllmProvider(base_url="http://127.0.0.1:30800", model="svd")
        with pytest.raises(RuntimeError, match="vLLM at"):
            await p.extract("text", "PMID1", PipelineConfig(), None)


class TestVllmProviderRetries:
    @pytest.mark.asyncio
    async def test_truncation_raises_extraction_failed(self, mocker):
        from pipeline_app_hpc.providers.vllm_provider import VllmProvider

        from pipeline.config import PipelineConfig
        from pipeline.llm_providers.base import ExtractionFailedError

        models_resp = MagicMock(status_code=200)
        models_resp.raise_for_status = MagicMock()
        chat_resp = _mock_chat_response(
            content='{"genes": [',
            finish_reason="length",
        )
        client_mock = AsyncMock(spec=httpx.AsyncClient)
        client_mock.get = AsyncMock(return_value=models_resp)
        client_mock.post = AsyncMock(return_value=chat_resp)
        client_mock.aclose = AsyncMock()
        mocker.patch(
            "pipeline_app_hpc.providers.vllm_provider.httpx.AsyncClient",
            return_value=client_mock,
        )

        p = VllmProvider(base_url="http://127.0.0.1:30800", model="svd")
        with pytest.raises(ExtractionFailedError, match="truncated"):
            await p.extract("t", "PMID1", PipelineConfig(), None)

    @pytest.mark.asyncio
    async def test_validation_retry_succeeds_on_second_try(self, mocker):
        from pipeline_app_hpc.providers.vllm_provider import VllmProvider

        from pipeline.config import PipelineConfig

        models_resp = MagicMock(status_code=200)
        models_resp.raise_for_status = MagicMock()
        bad_resp = _mock_chat_response(content="not json")
        good_resp = _mock_chat_response(
            content=json.dumps(
                {
                    "genes": [
                        {
                            "gene_symbol": "APOE",
                            "confidence": 0.8,
                            "gwas_trait": [],
                            "omics_evidence": [],
                            "mendelian_randomization": False,
                        }
                    ]
                }
            )
        )

        client_mock = AsyncMock(spec=httpx.AsyncClient)
        client_mock.get = AsyncMock(return_value=models_resp)
        client_mock.post = AsyncMock(side_effect=[bad_resp, good_resp])
        client_mock.aclose = AsyncMock()
        mocker.patch(
            "pipeline_app_hpc.providers.vllm_provider.httpx.AsyncClient",
            return_value=client_mock,
        )

        p = VllmProvider(base_url="http://127.0.0.1:30800", model="svd")
        cfg = PipelineConfig()
        cfg.max_retries = 1
        genes, _ = await p.extract("t", "PMID1", cfg, None)
        assert len(genes) == 1
        assert client_mock.post.call_count == 2

    @pytest.mark.asyncio
    async def test_validation_retry_exhausted(self, mocker):
        from pipeline_app_hpc.providers.vllm_provider import VllmProvider

        from pipeline.config import PipelineConfig
        from pipeline.llm_providers.base import ExtractionFailedError

        models_resp = MagicMock(status_code=200)
        models_resp.raise_for_status = MagicMock()
        bad_resp = _mock_chat_response(content="not json")
        client_mock = AsyncMock(spec=httpx.AsyncClient)
        client_mock.get = AsyncMock(return_value=models_resp)
        client_mock.post = AsyncMock(return_value=bad_resp)
        client_mock.aclose = AsyncMock()
        mocker.patch(
            "pipeline_app_hpc.providers.vllm_provider.httpx.AsyncClient",
            return_value=client_mock,
        )

        p = VllmProvider(base_url="http://127.0.0.1:30800", model="svd")
        cfg = PipelineConfig()
        cfg.max_retries = 1
        with pytest.raises(ExtractionFailedError, match="validation retries"):
            await p.extract("t", "PMID1", cfg, None)
        assert client_mock.post.call_count == 2

    @pytest.mark.asyncio
    async def test_connection_retry_then_success(self, mocker):
        from pipeline_app_hpc.providers.vllm_provider import VllmProvider

        from pipeline.config import PipelineConfig

        models_resp = MagicMock(status_code=200)
        models_resp.raise_for_status = MagicMock()
        good_resp = _mock_chat_response(content=json.dumps({"genes": []}))

        client_mock = AsyncMock(spec=httpx.AsyncClient)
        client_mock.get = AsyncMock(return_value=models_resp)
        client_mock.post = AsyncMock(
            side_effect=[
                httpx.ConnectError("flap"),
                good_resp,
            ]
        )
        client_mock.aclose = AsyncMock()
        mocker.patch(
            "pipeline_app_hpc.providers.vllm_provider.httpx.AsyncClient",
            return_value=client_mock,
        )
        mocker.patch(
            "pipeline_app_hpc.providers.vllm_provider.asyncio.sleep",
            new=AsyncMock(),
        )

        p = VllmProvider(base_url="http://127.0.0.1:30800", model="svd")
        cfg = PipelineConfig()
        cfg.max_connection_retries = 3
        genes, _ = await p.extract("t", "PMID1", cfg, None)
        assert genes == []
        assert client_mock.post.call_count == 2

    @pytest.mark.asyncio
    async def test_connection_retry_exhausted(self, mocker):
        from pipeline_app_hpc.providers.vllm_provider import VllmProvider

        from pipeline.config import PipelineConfig
        from pipeline.llm_providers.base import ExtractionFailedError

        models_resp = MagicMock(status_code=200)
        models_resp.raise_for_status = MagicMock()
        client_mock = AsyncMock(spec=httpx.AsyncClient)
        client_mock.get = AsyncMock(return_value=models_resp)
        client_mock.post = AsyncMock(side_effect=httpx.ConnectError("down"))
        client_mock.aclose = AsyncMock()
        mocker.patch(
            "pipeline_app_hpc.providers.vllm_provider.httpx.AsyncClient",
            return_value=client_mock,
        )
        mocker.patch(
            "pipeline_app_hpc.providers.vllm_provider.asyncio.sleep",
            new=AsyncMock(),
        )

        p = VllmProvider(base_url="http://127.0.0.1:30800", model="svd")
        cfg = PipelineConfig()
        cfg.max_connection_retries = 2
        with pytest.raises(ExtractionFailedError, match="connection retries"):
            await p.extract("t", "PMID1", cfg, None)

    @pytest.mark.asyncio
    async def test_429_rate_limit_retry_then_success(self, mocker):
        from pipeline_app_hpc.providers.vllm_provider import VllmProvider

        from pipeline.config import PipelineConfig

        models_resp = MagicMock(status_code=200)
        models_resp.raise_for_status = MagicMock()
        rl_err = MagicMock(status_code=429)
        rl_err.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "429", request=MagicMock(), response=rl_err
            )
        )
        good_resp = _mock_chat_response(content=json.dumps({"genes": []}))

        client_mock = AsyncMock(spec=httpx.AsyncClient)
        client_mock.get = AsyncMock(return_value=models_resp)
        client_mock.post = AsyncMock(side_effect=[rl_err, good_resp])
        client_mock.aclose = AsyncMock()
        mocker.patch(
            "pipeline_app_hpc.providers.vllm_provider.httpx.AsyncClient",
            return_value=client_mock,
        )
        mocker.patch(
            "pipeline_app_hpc.providers.vllm_provider.asyncio.sleep",
            new=AsyncMock(),
        )

        p = VllmProvider(base_url="http://127.0.0.1:30800", model="svd")
        cfg = PipelineConfig()
        cfg.max_rate_limit_retries = 3
        genes, _ = await p.extract("t", "PMID1", cfg, None)
        assert genes == []
        assert client_mock.post.call_count == 2

    @pytest.mark.asyncio
    async def test_503_5xx_retry_then_success(self, mocker):
        from pipeline_app_hpc.providers.vllm_provider import VllmProvider

        from pipeline.config import PipelineConfig

        models_resp = MagicMock(status_code=200)
        models_resp.raise_for_status = MagicMock()
        srv_err = MagicMock(status_code=503)
        srv_err.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "503", request=MagicMock(), response=srv_err
            )
        )
        good_resp = _mock_chat_response(content=json.dumps({"genes": []}))

        client_mock = AsyncMock(spec=httpx.AsyncClient)
        client_mock.get = AsyncMock(return_value=models_resp)
        client_mock.post = AsyncMock(side_effect=[srv_err, good_resp])
        client_mock.aclose = AsyncMock()
        mocker.patch(
            "pipeline_app_hpc.providers.vllm_provider.httpx.AsyncClient",
            return_value=client_mock,
        )
        mocker.patch(
            "pipeline_app_hpc.providers.vllm_provider.asyncio.sleep",
            new=AsyncMock(),
        )

        p = VllmProvider(base_url="http://127.0.0.1:30800", model="svd")
        cfg = PipelineConfig()
        cfg.max_connection_retries = 3
        genes, _ = await p.extract("t", "PMID1", cfg, None)
        assert genes == []
        assert client_mock.post.call_count == 2

    @pytest.mark.asyncio
    async def test_rate_limiter_acquired_when_provided(self, mocker):
        from pipeline_app_hpc.providers.vllm_provider import VllmProvider

        from pipeline.config import PipelineConfig

        models_resp = MagicMock(status_code=200)
        models_resp.raise_for_status = MagicMock()
        good_resp = _mock_chat_response(content=json.dumps({"genes": []}))

        client_mock = AsyncMock(spec=httpx.AsyncClient)
        client_mock.get = AsyncMock(return_value=models_resp)
        client_mock.post = AsyncMock(return_value=good_resp)
        client_mock.aclose = AsyncMock()
        mocker.patch(
            "pipeline_app_hpc.providers.vllm_provider.httpx.AsyncClient",
            return_value=client_mock,
        )

        rate_limiter = MagicMock()
        rate_limiter.acquire = AsyncMock(return_value=123)
        rate_limiter.record_actual_usage = AsyncMock()

        p = VllmProvider(base_url="http://127.0.0.1:30800", model="svd")
        await p.extract("t", "PMID1", PipelineConfig(), rate_limiter)
        rate_limiter.acquire.assert_awaited()
        rate_limiter.record_actual_usage.assert_awaited_once_with(123, 150)

    @pytest.mark.asyncio
    async def test_rate_limiter_reservation_released_on_transport_error(
        self, mocker
    ):
        from pipeline_app_hpc.providers.vllm_provider import VllmProvider

        from pipeline.config import PipelineConfig
        from pipeline.llm_providers.base import ExtractionFailedError

        models_resp = MagicMock(status_code=200)
        models_resp.raise_for_status = MagicMock()

        client_mock = AsyncMock(spec=httpx.AsyncClient)
        client_mock.get = AsyncMock(return_value=models_resp)
        client_mock.post = AsyncMock(side_effect=httpx.ReadTimeout("timeout"))
        client_mock.aclose = AsyncMock()
        mocker.patch(
            "pipeline_app_hpc.providers.vllm_provider.httpx.AsyncClient",
            return_value=client_mock,
        )
        mocker.patch(
            "pipeline_app_hpc.providers.vllm_provider.asyncio.sleep",
            new=AsyncMock(),
        )

        rate_limiter = MagicMock()
        rate_limiter.acquire = AsyncMock(return_value=456)
        rate_limiter.record_actual_usage = AsyncMock()

        cfg = PipelineConfig()
        cfg.max_connection_retries = 0
        p = VllmProvider(base_url="http://127.0.0.1:30800", model="svd")
        with pytest.raises(ExtractionFailedError, match="connection retries"):
            await p.extract("t", "PMID1", cfg, rate_limiter)

        rate_limiter.record_actual_usage.assert_awaited_once_with(456, 0)

    @pytest.mark.asyncio
    async def test_accumulated_usage_carries_through_structural_malformation(
        self, mocker
    ):
        # vLLM (or a misconfigured proxy) can return a structurally malformed
        # /v1/chat/completions response — e.g. streaming-shaped `delta` instead
        # of `message` — alongside a valid `usage` block. The KeyError on
        # `choice["message"]` is caught by the outer except and retried, but
        # the spent tokens must still be reflected in the accumulated usage
        # carried by ExtractionFailedError when retries are exhausted.
        # Otherwise downstream cost reports under-count by one full attempt
        # per malformed structure.
        from pipeline_app_hpc.providers.vllm_provider import VllmProvider

        from pipeline.config import PipelineConfig
        from pipeline.llm_providers.base import ExtractionFailedError

        models_resp = MagicMock(status_code=200)
        models_resp.raise_for_status = MagicMock()

        malformed = MagicMock(spec=httpx.Response)
        malformed.status_code = 200
        malformed.json = MagicMock(
            return_value={
                # `delta` instead of `message` triggers KeyError on
                # `choice["message"]["content"]`.
                "choices": [{"delta": {"content": "x"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 800, "completion_tokens": 400},
            }
        )
        malformed.raise_for_status = MagicMock()

        client_mock = AsyncMock(spec=httpx.AsyncClient)
        client_mock.get = AsyncMock(return_value=models_resp)
        client_mock.post = AsyncMock(return_value=malformed)
        client_mock.aclose = AsyncMock()
        mocker.patch(
            "pipeline_app_hpc.providers.vllm_provider.httpx.AsyncClient",
            return_value=client_mock,
        )

        p = VllmProvider(base_url="http://127.0.0.1:30800", model="svd")
        cfg = PipelineConfig()
        cfg.max_retries = 1
        with pytest.raises(ExtractionFailedError) as excinfo:
            await p.extract("t", "PMID1", cfg, None)

        # Two attempts × (800 prompt + 400 completion) = 2400 total.
        usage = excinfo.value.token_usage
        assert usage is not None
        assert usage.input_tokens == 800 * 2
        assert usage.output_tokens == 400 * 2

    @pytest.mark.asyncio
    async def test_rate_limiter_signals_global_backoff_on_429(self, mocker):
        from pipeline_app_hpc.providers.vllm_provider import VllmProvider

        from pipeline.config import PipelineConfig

        models_resp = MagicMock(status_code=200)
        models_resp.raise_for_status = MagicMock()
        rl_resp = MagicMock(status_code=429)
        rl_resp.headers = {"retry-after": "3"}
        rl_resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "429", request=MagicMock(), response=rl_resp
            )
        )
        good_resp = _mock_chat_response(content=json.dumps({"genes": []}))

        client_mock = AsyncMock(spec=httpx.AsyncClient)
        client_mock.get = AsyncMock(return_value=models_resp)
        client_mock.post = AsyncMock(side_effect=[rl_resp, good_resp])
        client_mock.aclose = AsyncMock()
        mocker.patch(
            "pipeline_app_hpc.providers.vllm_provider.httpx.AsyncClient",
            return_value=client_mock,
        )
        sleep_mock = mocker.patch(
            "pipeline_app_hpc.providers.vllm_provider.asyncio.sleep",
            new=AsyncMock(),
        )

        rate_limiter = MagicMock()
        rate_limiter.acquire = AsyncMock(side_effect=[1, 2])
        rate_limiter.record_actual_usage = AsyncMock()
        rate_limiter.signal_rate_limit = AsyncMock()

        cfg = PipelineConfig()
        cfg.max_rate_limit_retries = 1
        p = VllmProvider(base_url="http://127.0.0.1:30800", model="svd")
        genes, _ = await p.extract("t", "PMID1", cfg, rate_limiter)

        assert genes == []
        rate_limiter.record_actual_usage.assert_any_await(1, 0)
        rate_limiter.record_actual_usage.assert_any_await(2, 150)
        rate_limiter.signal_rate_limit.assert_awaited_once_with(3.0)
        sleep_mock.assert_awaited_once_with(3.0)
