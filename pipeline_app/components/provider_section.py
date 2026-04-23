"""Shared helper for provider-aware widget toggling across pages."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

    from pipeline.config import LLMProviderName


def apply_provider_widget_state(
    provider: LLMProviderName,
    *,
    claude_widgets: Sequence[Any],
) -> bool:
    """Enable Claude-only widgets when the provider is anthropic.

    Returns ``is_ollama`` so callers can drive page-specific follow-up
    behavior (Ollama-section visibility, live tag refresh, prompt swap).
    Centralises the ``llm_provider == "ollama"`` derivation so a widget
    added to one page doesn't drift from the other.
    """
    is_ollama = provider == "ollama"
    for widget in claude_widgets:
        widget.set_enabled(not is_ollama)
    return is_ollama
