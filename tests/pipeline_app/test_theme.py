"""Tests for theme configuration helpers."""

from __future__ import annotations

from pipeline_app.theme import (
    CHART_ACCENT_COLORS,
    COLORS,
    chart_axis_label,
    chart_split_line,
    chart_title,
)


class TestColors:
    def test_has_required_keys(self):
        required = {
            "primary",
            "secondary",
            "accent",
            "info",
            "warning",
            "negative",
            "dark",
            "dark_page",
            "elevated",
            "overlay",
            "text_primary",
            "text_secondary",
            "text_disabled",
        }
        assert required.issubset(COLORS.keys())

    def test_all_values_are_hex_strings(self):
        for key, val in COLORS.items():
            assert isinstance(val, str) and val.startswith("#"), (
                f"COLORS[{key!r}] = {val!r} is not a hex color string"
            )


class TestChartAccentColors:
    def test_has_five_entries(self):
        assert len(CHART_ACCENT_COLORS) == 5

    def test_all_are_hex_strings(self):
        for color in CHART_ACCENT_COLORS:
            assert isinstance(color, str) and color.startswith("#")

    def test_includes_primary_and_secondary(self):
        assert COLORS["primary"] in CHART_ACCENT_COLORS
        assert COLORS["secondary"] in CHART_ACCENT_COLORS


class TestChartTitle:
    def test_contains_text(self):
        result = chart_title("My Title")
        assert result["text"] == "My Title"

    def test_centered(self):
        assert chart_title("T")["left"] == "center"

    def test_has_text_style_with_color(self):
        style = chart_title("T")["textStyle"]
        assert "color" in style
        assert "fontSize" in style
        assert "fontFamily" in style


class TestChartAxisLabel:
    def test_returns_color(self):
        assert "color" in chart_axis_label()

    def test_returns_font_size(self):
        assert chart_axis_label()["fontSize"] == 10


class TestChartSplitLine:
    def test_returns_line_style_with_color(self):
        result = chart_split_line()
        assert "lineStyle" in result
        assert "color" in result["lineStyle"]
