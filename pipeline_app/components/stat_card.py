"""Stat card primitive — number-dominant metric surface."""

from __future__ import annotations

import math
from typing import Literal, get_args

from nicegui import ui

StatColor = Literal["primary", "secondary", "warning", "negative", "info"]

_VALID_COLORS: frozenset[str] = frozenset(get_args(StatColor))

# Em-dash placeholder for missing/NaN values. Rendering ``"None"`` or
# ``"nan"`` verbatim as a prominent metric is a visible data-quality bug
# — callers that genuinely want zero should pass ``0`` explicitly.
_MISSING_DISPLAY: str = "—"


def _format_value(value: str | int | float | None) -> str:
    """Render a stat-card value, guarding against None and NaN."""
    if value is None:
        return _MISSING_DISPLAY
    if isinstance(value, float) and math.isnan(value):
        return _MISSING_DISPLAY
    return str(value)


def stat_card(
    value: str | int | float | None,
    label: str,
    *,
    icon: str | None = None,
    delta: str | None = None,
    delta_positive: bool | None = None,
    color: StatColor = "primary",
) -> ui.card:
    """Render a number-dominant stat card.

    Extends the existing ``.stat-card`` CSS primitive with an optional
    Material-icon glyph in the top-right and an optional delta chip below
    the value. The delta tone is inferred from ``delta_positive`` (True →
    positive, False → negative, None → neutral).

    Args:
        value: The primary metric to display (rendered large, tabular nums).
        label: Short eyebrow label beneath the value.
        icon: Optional Material icon name shown at top-right.
        delta: Optional short secondary metric (e.g. "+12%", "-3").
        delta_positive: Tone for ``delta``. None → neutral gray chip.
        color: Left-border accent color.

    Returns:
        The wrapping ui.card so callers can add further styling or content.
    """
    if color not in _VALID_COLORS:
        raise ValueError(
            f"Invalid stat_card color {color!r}. "
            f"Expected one of {sorted(_VALID_COLORS)}."
        )

    card = ui.card().classes(f"stat-card accent-{color}")
    with card:
        if icon:
            ui.icon(icon).classes("stat-card-icon")
        ui.label(label).classes("eyebrow q-mb-xs")
        ui.label(_format_value(value)).classes("stat-card-value-display")
        if delta is not None:
            tone = (
                "delta-positive"
                if delta_positive is True
                else "delta-negative"
                if delta_positive is False
                else "delta-neutral"
            )
            ui.label(delta).classes(f"stat-card-delta {tone}")
    return card
