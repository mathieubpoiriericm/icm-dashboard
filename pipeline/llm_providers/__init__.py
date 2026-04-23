"""LLM provider abstraction — pluggable backends for gene extraction."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from pipeline.llm_providers.base import (
    ExtractionResult,
    GeneEntry,
    LLMProvider,
)

if TYPE_CHECKING:
    from pipeline.config import PipelineConfig


def _build_anthropic(config: PipelineConfig) -> LLMProvider:
    from pipeline.llm_providers.anthropic_provider import AnthropicProvider

    return AnthropicProvider()


def _build_ollama(config: PipelineConfig) -> LLMProvider:
    from pipeline.llm_providers.ollama_provider import OllamaProvider

    return OllamaProvider(
        host=config.ollama_host,
        model=config.ollama_model,
        num_ctx=config.ollama_num_ctx,
        keep_alive=config.ollama_keep_alive,
    )


# Provider modules are imported lazily so pulling GeneEntry from this
# package does not load the Anthropic or Ollama SDKs unnecessarily.
_PROVIDER_BUILDERS: dict[str, Callable[[PipelineConfig], LLMProvider]] = {
    "anthropic": _build_anthropic,
    "ollama": _build_ollama,
}


def get_provider(config: PipelineConfig) -> LLMProvider:
    """Return the provider implementation matching ``config.llm_provider``.

    ``PipelineConfig.__post_init__`` already validates ``llm_provider`` against
    ``LLM_PROVIDERS``, so the lookup is always present here.
    """
    return _PROVIDER_BUILDERS[config.llm_provider](config)


__all__ = [
    "ExtractionResult",
    "GeneEntry",
    "LLMProvider",
    "get_provider",
]
