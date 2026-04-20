"""Theme configuration — colors, fonts, and CSS injection."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nicegui import app, ui

_THEME_CSS = Path(__file__).parent / "static" / "theme.css"

_GOOGLE_FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2'
    "?family=Inter:wght@400;500;600;700"
    "&family=JetBrains+Mono:wght@400"
    # Bricolage Grotesque: variable display face used for titles and stat
    # values. opsz axis is referenced from theme.css; opsz range 12–96 covers
    # everything from small labels up to the .display-xl size.
    "&family=Bricolage+Grotesque:opsz,wght@12..96,400;12..96,600;12..96,700"
    '&display=swap" rel="stylesheet">'
)

# Single source of truth for the color palette.
# Referenced by app.colors(), theme.css (via matching custom properties),
# and ECharts configs (which can't read CSS vars server-side).
COLORS: dict[str, str] = {
    "primary": "#3B5BDB",
    "secondary": "#00D4AA",
    "info": "#54A0FF",
    "warning": "#FFB547",
    "negative": "#FF5C7C",
    "dark": "#0B0F1A",
    "dark_page": "#131829",
    "elevated": "#1C2237",
    "overlay": "#252B44",
    "text_primary": "#E8ECF4",
    "text_secondary": "#8B95B0",
    "text_disabled": "#4A5272",
}

CHART_ACCENT_COLORS: list[str] = [
    COLORS["primary"],
    COLORS["secondary"],
    COLORS["info"],
    COLORS["warning"],
    COLORS["negative"],
]


def chart_title(text: str) -> dict[str, Any]:
    """Return a standard ECharts title config."""
    return {
        "text": text,
        "left": "center",
        "top": "5%",
        "textStyle": {
            "color": COLORS["text_secondary"],
            "fontSize": 13,
            "fontFamily": "Bricolage Grotesque, Inter, sans-serif",
            "fontWeight": 600,
        },
    }


def chart_axis_label() -> dict[str, Any]:
    """Return a standard ECharts axis label style."""
    return {"color": COLORS["text_disabled"], "fontSize": 10}


def chart_split_line() -> dict[str, Any]:
    """Return a standard ECharts split line style."""
    return {"lineStyle": {"color": COLORS["elevated"]}}


def apply_theme() -> None:
    """Apply the custom theme: Quasar colors, fonts, and CSS."""
    app.colors(
        primary=COLORS["primary"],
        secondary=COLORS["secondary"],
        dark=COLORS["dark"],
        dark_page=COLORS["dark_page"],
        positive=COLORS["secondary"],
        negative=COLORS["negative"],
        info=COLORS["info"],
        warning=COLORS["warning"],
    )
    ui.add_head_html(_GOOGLE_FONTS, shared=True)
    if _THEME_CSS.is_file():
        ui.add_css(_THEME_CSS.read_text(encoding="utf-8"), shared=True)
