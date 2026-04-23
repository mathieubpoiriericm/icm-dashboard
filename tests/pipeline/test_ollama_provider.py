"""Unit tests for OllamaProvider (mocked — no real Ollama server)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import ollama
import pytest

from pipeline.config import PipelineConfig
from pipeline.llm_providers.base import ExtractionResult
from pipeline.llm_providers.ollama_provider import OllamaProvider


def _chat_response(
    content: str,
    eval_count: int = 5,
    prompt_eval_count: int = 50,
    done_reason: str = "stop",
) -> ollama.ChatResponse:
    """Build a realistic ChatResponse for use in mock return values."""
    return ollama.ChatResponse(
        model="gemma4:e4b",
        message=ollama.Message(role="assistant", content=content),
        eval_count=eval_count,
        prompt_eval_count=prompt_eval_count,
        done_reason=done_reason,
    )


@pytest.fixture
def provider_with_mock_client(monkeypatch):
    """Build an OllamaProvider whose AsyncClient is a MagicMock we can script."""
    list_response = MagicMock()
    list_response.models = [MagicMock(model="gemma4:e4b")]

    mock_client = MagicMock()
    mock_client.list = AsyncMock(return_value=list_response)
    mock_client.chat = AsyncMock()

    monkeypatch.setattr(ollama, "AsyncClient", lambda host=None: mock_client)

    p = OllamaProvider(
        host="http://localhost:11434",
        model="gemma4:e4b",
        num_ctx=65_536,
    )
    return p, mock_client


@pytest.mark.asyncio
async def test_provider_name(provider_with_mock_client):
    p, _ = provider_with_mock_client
    assert p.name == "ollama"


@pytest.mark.asyncio
async def test_supports_flags(provider_with_mock_client):
    p, _ = provider_with_mock_client
    assert p.supports_thinking() is False
    assert p.supports_prompt_caching() is False


@pytest.mark.asyncio
async def test_health_check_on_first_extract(provider_with_mock_client):
    p, client = provider_with_mock_client
    client.chat.return_value = _chat_response(
        '{"genes": []}', eval_count=10, prompt_eval_count=50
    )
    cfg = PipelineConfig(llm_provider="ollama", prompt_version="ollama_v1")
    await p.extract("text", "1", cfg, None)
    client.list.assert_awaited_once()


@pytest.mark.asyncio
async def test_health_check_failure_raises_clear_error(monkeypatch):
    bad_client = MagicMock()
    bad_client.list = AsyncMock(side_effect=ConnectionError("refused"))
    monkeypatch.setattr(ollama, "AsyncClient", lambda host=None: bad_client)

    p = OllamaProvider(host="http://localhost:11434", model="x", num_ctx=1024)
    cfg = PipelineConfig(llm_provider="ollama")
    with pytest.raises(RuntimeError, match="Ollama.*not reachable"):
        await p.extract("text", "1", cfg, None)


@pytest.mark.asyncio
async def test_close_is_idempotent(provider_with_mock_client):
    p, _ = provider_with_mock_client
    await p.close()
    await p.close()  # second call must not raise


@pytest.mark.asyncio
async def test_extract_happy_path(provider_with_mock_client):
    p, client = provider_with_mock_client
    client.chat.return_value = _chat_response(
        '{"genes": [{"gene_symbol": "NOTCH3", "confidence": 0.9, '
        '"gwas_trait": ["SVS"], "mendelian_randomization": false, '
        '"omics_evidence": []}]}',
        eval_count=150,
        prompt_eval_count=2000,
    )
    cfg = PipelineConfig(llm_provider="ollama", prompt_version="ollama_v1")
    genes, usage = await p.extract("paper text", "12345", cfg, None)
    assert len(genes) == 1
    assert genes[0].gene_symbol == "NOTCH3"
    assert genes[0].pmid == "12345"
    assert usage.input_tokens == 2000
    assert usage.output_tokens == 150


@pytest.mark.asyncio
async def test_extract_passes_schema_to_ollama(provider_with_mock_client):
    p, client = provider_with_mock_client
    client.chat.return_value = _chat_response('{"genes": []}')
    cfg = PipelineConfig(llm_provider="ollama", prompt_version="ollama_v1")
    await p.extract("t", "1", cfg, None)

    _, kwargs = client.chat.call_args
    assert kwargs["model"] == "gemma4:e4b"
    assert kwargs["format"] == ExtractionResult.model_json_schema()
    assert kwargs["options"]["num_ctx"] == 65_536
    assert kwargs["options"]["temperature"] == 0.0
    assert kwargs["stream"] is False
    assert kwargs["keep_alive"] == "30m"


@pytest.mark.asyncio
async def test_extract_empty_genes(provider_with_mock_client):
    p, client = provider_with_mock_client
    client.chat.return_value = _chat_response('{"genes": []}')
    cfg = PipelineConfig(llm_provider="ollama", prompt_version="ollama_v1")
    genes, _ = await p.extract("t", "1", cfg, None)
    assert genes == []


@pytest.mark.asyncio
async def test_extract_retries_on_validation_error(provider_with_mock_client):
    p, client = provider_with_mock_client
    # First call returns malformed JSON. Second call returns valid.
    client.chat.side_effect = [
        _chat_response("not json"),
        _chat_response('{"genes": []}'),
    ]
    cfg = PipelineConfig(
        llm_provider="ollama",
        prompt_version="ollama_v1",
        max_retries=2,
    )
    genes, _ = await p.extract("t", "1", cfg, None)
    assert genes == []
    assert client.chat.await_count == 2


@pytest.mark.asyncio
async def test_extract_retries_on_pydantic_validation_error(provider_with_mock_client):
    p, client = provider_with_mock_client
    client.chat.side_effect = [
        _chat_response('{"genes": [{"gene_symbol": "X", "confidence": 2.0}]}'),
        _chat_response('{"genes": []}'),
    ]
    cfg = PipelineConfig(
        llm_provider="ollama",
        prompt_version="ollama_v1",
        max_retries=2,
    )
    genes, _ = await p.extract("t", "1", cfg, None)
    assert genes == []


@pytest.mark.asyncio
async def test_extract_gives_up_after_max_retries(provider_with_mock_client):
    p, client = provider_with_mock_client
    client.chat.return_value = _chat_response("still not json")
    cfg = PipelineConfig(
        llm_provider="ollama",
        prompt_version="ollama_v1",
        max_retries=1,
    )
    with pytest.raises(json.JSONDecodeError):
        await p.extract("t", "1", cfg, None)
    # max_retries=1 → 1 initial + 1 retry = 2 calls total.
    assert client.chat.await_count == 2


@pytest.mark.asyncio
async def test_extract_retries_httpx_connect_error(provider_with_mock_client):
    import httpx

    p, client = provider_with_mock_client
    client.chat.side_effect = [
        httpx.ConnectError("refused"),
        _chat_response('{"genes": []}'),
    ]
    cfg = PipelineConfig(
        llm_provider="ollama",
        prompt_version="ollama_v1",
        max_connection_retries=2,
        connection_retry_delay=0.0,
    )
    genes, _ = await p.extract("t", "1", cfg, None)
    assert genes == []
    assert client.chat.await_count == 2


@pytest.mark.asyncio
async def test_extract_gives_up_on_repeated_connect_errors(provider_with_mock_client):
    import httpx

    p, client = provider_with_mock_client
    client.chat.side_effect = httpx.ConnectError("refused")
    cfg = PipelineConfig(
        llm_provider="ollama",
        prompt_version="ollama_v1",
        max_connection_retries=2,
        connection_retry_delay=0.0,
    )
    with pytest.raises(httpx.ConnectError):
        await p.extract("t", "1", cfg, None)
    assert client.chat.await_count == 3  # initial + 2 retries


@pytest.mark.asyncio
async def test_extract_bails_on_truncated_response(provider_with_mock_client):
    """Ollama done_reason='length' must short-circuit without parsing.

    Parity with AnthropicProvider's stop_reason=='max_tokens' handling:
    truncation is deterministic under a fixed num_predict, so burning
    validation retries to re-truncate is waste, and a JSON-constrained
    output can still pass `parse_extraction_response` on a truncated-
    but-structurally-valid prefix.
    """
    p, client = provider_with_mock_client
    client.chat.return_value = _chat_response(
        '{"genes": [{"gene_symbol": "NOTCH3", "confidence": 0.9, '
        '"gwas_trait": ["SVS"], "mendelian_randomization": false, '
        '"omics_evidence": []}]}',
        eval_count=64,
        prompt_eval_count=2000,
        done_reason="length",
    )
    cfg = PipelineConfig(llm_provider="ollama", prompt_version="ollama_v1")
    genes, usage = await p.extract("t", "42", cfg, None)
    assert genes == []
    assert usage.input_tokens == 2000
    assert usage.output_tokens == 64
    # Exactly one call — no validation retries burned.
    assert client.chat.await_count == 1
