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
