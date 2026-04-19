"""Skeleton loader primitives — shimmering placeholders for async loads."""

from __future__ import annotations

from nicegui import ui


def skeleton_card(rows: int = 3) -> ui.element:
    """Render a single card-shaped skeleton with N shimmering rows.

    Args:
        rows: Number of placeholder rows (minimum 1). The first row is
            rendered as a wider title block; remaining rows alternate
            full/medium/short widths for a natural rhythm.

    Returns:
        The outer container so the caller can ``.delete()`` it once real
        content is ready.
    """
    rows = max(1, int(rows))
    widths = ["skeleton-row-medium", "", "skeleton-row-short"]

    container = ui.element("div").classes("skeleton-card")
    with container:
        ui.element("div").classes("skeleton skeleton-row skeleton-row-title")
        for i in range(rows):
            width_cls = widths[i % len(widths)]
            ui.element("div").classes(f"skeleton skeleton-row {width_cls}".strip())
    return container


def skeleton_table(rows: int = 5, cols: int = 4) -> ui.element:
    """Render a table-shaped skeleton with a header row and N body rows.

    Args:
        rows: Number of body rows (minimum 1).
        cols: Number of columns per row (minimum 1).

    Returns:
        The outer container; ``.delete()`` once the real table is ready.
    """
    rows = max(1, int(rows))
    cols = max(1, int(cols))

    container = ui.element("div").classes("skeleton-table")
    with container:
        with ui.element("div").classes("skeleton-table-row skeleton-table-header"):
            for _ in range(cols):
                ui.element("div").classes("skeleton skeleton-table-cell")
        for _ in range(rows):
            with ui.element("div").classes("skeleton-table-row"):
                for _ in range(cols):
                    ui.element("div").classes("skeleton skeleton-table-cell")
    return container


def skeleton_stat_grid(count: int = 4) -> ui.element:
    """Render a responsive grid of stat-card-shaped skeletons.

    Args:
        count: Number of placeholder stat cards (minimum 1).

    Returns:
        The outer grid container; ``.delete()`` when real stats arrive.
    """
    count = max(1, int(count))

    container = ui.element("div").classes("skeleton-stat-grid")
    with container:
        for _ in range(count):
            with ui.element("div").classes("skeleton-stat-item"):
                ui.element("div").classes("skeleton skeleton-row skeleton-row-short")
                ui.element("div").classes("skeleton skeleton-row skeleton-row-title")
    return container
