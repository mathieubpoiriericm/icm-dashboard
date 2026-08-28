"""LLM provider abstraction — pluggable backends for gene extraction."""

from collections.abc import Callable
from typing import TYPE_CHECKING

from pipeline.llm_providers.base import (
    ExtractionFailedError,
    ExtractionResult,
    GeneEntry,
    LLMProvider,
)

if TYPE_CHECKING:
    from pipeline.config import PipelineConfig


def _build_anthropic() -> LLMProvider:
    from pipeline.llm_providers.anthropic_provider import AnthropicProvider

    return AnthropicProvider()


# Provider modules are imported lazily so pulling GeneEntry from this
# package does not load the Anthropic SDK unnecessarily.
_PROVIDER_BUILDERS: dict[str, Callable[[], LLMProvider]] = {
    "anthropic": _build_anthropic,
}


def get_provider(config: PipelineConfig) -> LLMProvider:
    """Return the provider implementation matching ``config.llm_provider``.

    ``PipelineConfig.__post_init__`` already validates ``llm_provider`` against
    ``LLM_PROVIDERS``, so the lookup is always present here.
    """
    return _PROVIDER_BUILDERS[config.llm_provider]()


__all__ = [
    "ExtractionFailedError",
    "ExtractionResult",
    "GeneEntry",
    "LLMProvider",
    "get_provider",
]
