"""Tests for the stat_card component."""

from __future__ import annotations

import pytest
from pipeline_app.components.stat_card import _VALID_COLORS, stat_card


class TestValidColors:
    def test_contains_all_expected(self):
        expected = {"primary", "secondary", "warning", "negative", "info"}
        assert expected == _VALID_COLORS


class TestStatCardRender:
    def test_returns_card(self):
        card = stat_card(42, "Papers")
        assert type(card).__name__ == "Card"

    @pytest.mark.parametrize(
        "color", ["primary", "secondary", "warning", "negative", "info"]
    )
    def test_accepts_each_valid_color(self, color: str):
        card = stat_card(1, "Label", color=color)  # type: ignore[arg-type]
        assert f"accent-{color}" in card.classes

    def test_applies_stat_card_base_class(self):
        card = stat_card(1, "Label")
        assert "stat-card" in card.classes

    def test_default_color_is_primary(self):
        card = stat_card(1, "Label")
        assert "accent-primary" in card.classes

    def test_rejects_invalid_color(self):
        with pytest.raises(ValueError, match="Invalid stat_card color"):
            stat_card(1, "Label", color="rainbow")  # type: ignore[arg-type]

    def test_accepts_numeric_and_string_values(self):
        stat_card(42, "Int")
        stat_card(3.14, "Float")
        stat_card("$1.23", "Formatted")

    def test_icon_is_optional(self):
        stat_card(1, "L")
        stat_card(1, "L", icon="history")

    def test_delta_is_optional(self):
        stat_card(1, "L")
        stat_card(1, "L", delta="+5", delta_positive=True)
        stat_card(1, "L", delta="-3", delta_positive=False)
        stat_card(1, "L", delta="0", delta_positive=None)


class TestDeltaToneInference:
    """Verify delta_positive → CSS tone mapping.

    The component renders the tone class as part of the final label, so we
    exercise the three branches via the public render path and inspect the
    resulting class list on the delta label (the last child with the
    ``stat-card-delta`` base class).
    """

    @staticmethod
    def _delta_classes(card) -> list[str]:
        for child in card:
            cls = getattr(child, "classes", [])
            if any(c.startswith("stat-card-delta") for c in cls):
                return cls
        raise AssertionError("no delta label rendered")

    def test_positive_gets_positive_tone(self):
        card = stat_card(1, "L", delta="+5", delta_positive=True)
        assert "delta-positive" in self._delta_classes(card)

    def test_negative_gets_negative_tone(self):
        card = stat_card(1, "L", delta="-5", delta_positive=False)
        assert "delta-negative" in self._delta_classes(card)

    def test_none_gets_neutral_tone(self):
        card = stat_card(1, "L", delta="0", delta_positive=None)
        assert "delta-neutral" in self._delta_classes(card)
