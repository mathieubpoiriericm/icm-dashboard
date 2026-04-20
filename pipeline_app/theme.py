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

# Quasar's QDrawer has no built-in resize affordance; this script
# appends a drag handle to the left drawer's right edge.
_DRAWER_RESIZER_JS = """
<script>
(function () {
  const MIN = 220, MAX = 500, KEY = 'csvd-pipeline-drawer-width';
  function init() {
    const d = document.querySelector('.q-drawer--left');
    if (!d || d.querySelector('.drawer-resizer')) return true;
    const p = document.querySelector('.q-page-container');
    const setWidth = (px) => {
      d.style.width = px + 'px';
      if (p) p.style.paddingLeft = px + 'px';
    };
    const h = document.createElement('div');
    h.className = 'drawer-resizer';
    d.appendChild(h);
    const saved = parseInt(localStorage.getItem(KEY) || '0', 10);
    if (saved >= MIN && saved <= MAX) setWidth(saved);
    h.addEventListener('mousedown', (e) => {
      e.preventDefault();
      h.classList.add('dragging');
      document.body.classList.add('drawer-resizing');
      const rect = d.getBoundingClientRect();
      const move = (ev) => {
        const w = Math.min(MAX, Math.max(MIN, ev.clientX - rect.left));
        setWidth(w);
      };
      const up = () => {
        h.classList.remove('dragging');
        document.body.classList.remove('drawer-resizing');
        document.removeEventListener('mousemove', move);
        document.removeEventListener('mouseup', up);
        const cur = parseInt(d.style.width, 10);
        if (cur >= MIN && cur <= MAX) localStorage.setItem(KEY, cur);
        window.dispatchEvent(new Event('resize'));
      };
      document.addEventListener('mousemove', move);
      document.addEventListener('mouseup', up);
    });
    h.addEventListener('dblclick', () => {
      localStorage.removeItem(KEY);
      d.style.width = '';
      if (p) p.style.paddingLeft = '';
      window.dispatchEvent(new Event('resize'));
    });
    return true;
  }
  // NiceGUI mounts .q-drawer after DOMContentLoaded; poll briefly.
  const iv = setInterval(() => { if (init()) clearInterval(iv); }, 50);
  setTimeout(() => clearInterval(iv), 5000);
})();
</script>
"""

# Single source of truth for the color palette — ICM Paper light theme.
# Referenced by app.colors(), theme.css (via matching custom properties),
# and ECharts configs (which can't read CSS vars server-side).
COLORS: dict[str, str] = {
    "primary": "#281E78",
    "secondary": "#0E8A5F",
    "info": "#2A5A9E",
    "warning": "#B85C00",
    "negative": "#C2185B",
    # "dark" / "dark_page" are Quasar token slots; repurposed as light
    # surfaces so Quasar plumbing and the test_theme.py key-set assertion
    # survive the light-theme flip.
    "dark": "#FAFAF7",
    "dark_page": "#FFFFFF",
    "elevated": "#F1EEE7",
    "overlay": "#E4E0D6",
    "text_primary": "#1A1430",
    "text_secondary": "#605A78",
    "text_disabled": "#A6A2B0",
}

# ICM brand orange; sits outside COLORS because Quasar has no token slot for it.
ACCENT_ORANGE: str = "#FA4616"

CHART_ACCENT_COLORS: list[str] = [
    COLORS["primary"],
    COLORS["secondary"],
    ACCENT_ORANGE,
    COLORS["info"],
    COLORS["warning"],
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
    return {"color": COLORS["text_secondary"], "fontSize": 10}


def chart_split_line() -> dict[str, Any]:
    """Return a standard ECharts split line style."""
    return {"lineStyle": {"color": COLORS["elevated"]}}


def apply_theme() -> None:
    """Apply the custom theme: Quasar colors, fonts, and CSS."""
    app.colors(
        primary=COLORS["primary"],
        # Route Quasar `secondary` to navy so `color="secondary"` stays on-brand;
        # emerald green still reaches the UI via `positive=COLORS["secondary"]`.
        secondary=COLORS["primary"],
        dark=COLORS["dark"],
        dark_page=COLORS["dark_page"],
        positive=COLORS["secondary"],
        negative=COLORS["negative"],
        info=COLORS["info"],
        warning=COLORS["warning"],
    )
    ui.add_head_html(_GOOGLE_FONTS, shared=True)
    ui.add_head_html(_DRAWER_RESIZER_JS, shared=True)
    if _THEME_CSS.is_file():
        ui.add_css(_THEME_CSS.read_text(encoding="utf-8"), shared=True)
