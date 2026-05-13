"""Theme for the HPC pipeline app — re-uses pipeline_app's theme."""

from __future__ import annotations

from pipeline_app.theme import apply_theme as _apply_base_theme


def apply_theme() -> None:
    """Apply the shared dark theme."""
    _apply_base_theme()
