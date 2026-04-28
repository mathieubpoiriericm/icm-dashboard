"""Tests for the provider dispatcher in pipeline.llm_extraction."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import pipeline.llm_extraction as le
from pipeline.config import PipelineConfig
from pipeline.llm_providers.anthropic_provider import AnthropicProvider


@pytest.fixture(autouse=True)
async def reset_provider():
    await le.close_async_client()
    yield
    await le.close_async_client()


@pytest.mark.asyncio
async def test_dispatch_uses_anthropic_by_default():
    cfg = PipelineConfig()
    assert cfg.llm_provider == "anthropic"
    with patch.object(AnthropicProvider, "extract", new_callable=AsyncMock) as mock:
        mock.return_value = ([], MagicMock())
        await le.extract_from_paper("text", "1", cfg, None)
    mock.assert_called_once()


@pytest.mark.asyncio
async def test_dispatch_caches_provider_across_calls():
    cfg = PipelineConfig()
    with patch.object(AnthropicProvider, "extract", new_callable=AsyncMock) as mock:
        mock.return_value = ([], MagicMock())
        await le.extract_from_paper("t1", "1", cfg, None)
        await le.extract_from_paper("t2", "2", cfg, None)
    assert mock.call_count == 2


@pytest.mark.asyncio
async def test_dispatch_swaps_provider_when_name_changes():
    """Anthropic → Ollama swap. OllamaProvider ships in Task 7; stub factory here."""

    class StubOllama:
        name = "ollama"

        def __init__(self):
            self.extract = AsyncMock(return_value=([], MagicMock()))
            self.close = AsyncMock()

        def supports_thinking(self):
            return False

        def supports_prompt_caching(self):
            return False

    stub = StubOllama()
    from pipeline import llm_providers

    def fake_factory(cfg):
        if cfg.llm_provider == "ollama":
            return stub
        return AnthropicProvider()

    # Patch both the package-level name and the re-imported name inside the module.
    with (
        patch.object(llm_providers, "get_provider", side_effect=fake_factory),
        patch("pipeline.llm_extraction.get_provider", side_effect=fake_factory),
        patch.object(
            AnthropicProvider, "extract", new_callable=AsyncMock
        ) as anth_mock,
    ):
        anth_mock.return_value = ([], MagicMock())
        cfg_a = PipelineConfig(llm_provider="anthropic")
        cfg_o = PipelineConfig(llm_provider="ollama")
        await le.extract_from_paper("t", "1", cfg_a, None)
        await le.extract_from_paper("t", "2", cfg_o, None)

    stub.extract.assert_called_once()


@pytest.mark.asyncio
async def test_dispatch_swaps_ollama_provider_when_model_changes():
    class StubOllama:
        name = "ollama"

        def __init__(self, label):
            self.label = label
            self.extract = AsyncMock(return_value=([], MagicMock()))
            self.close = AsyncMock()

        def supports_thinking(self):
            return False

        def supports_prompt_caching(self):
            return False

    stubs: list[StubOllama] = []

    def fake_factory(cfg):
        stub = StubOllama(cfg.ollama_model)
        stubs.append(stub)
        return stub

    with patch("pipeline.llm_extraction.get_provider", side_effect=fake_factory):
        cfg_a = PipelineConfig(llm_provider="ollama", ollama_model="model-a")
        cfg_b = PipelineConfig(llm_provider="ollama", ollama_model="model-b")
        await le.extract_from_paper("t", "1", cfg_a, None)
        await le.extract_from_paper("t", "2", cfg_b, None)

    assert [s.label for s in stubs] == ["model-a", "model-b"]
    stubs[0].close.assert_awaited_once()
