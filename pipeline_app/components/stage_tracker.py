"""Reusable stage progress indicator component — vertical timeline."""

from __future__ import annotations

from nicegui import ui

STAGE_DOT_CLASSES: dict[str, str] = {
    "pending": "dot-pending",
    "running": "dot-running",
    "completed": "dot-completed",
    "failed": "dot-failed",
    "skipped": "dot-skipped",
}

STAGE_LABEL_CLASSES: dict[str, str] = {
    "running": "label-running",
    "completed": "label-completed",
    "failed": "label-failed",
}


def create_stage_tracker(
    stages: list[str],
    stage_statuses: dict[str, str],
    current_repeat: int = 0,
    total_repeats: int = 0,
) -> None:
    """Render a vertical timeline of stages with status dots."""
    if current_repeat > 0 and total_repeats > 1:
        ui.html(
            f'<span class="repeat-counter">'
            f"Repeat {current_repeat}/{total_repeats}"
            f"</span>"
        )

    with ui.element("div").classes("stage-timeline"):
        for stage in stages:
            status = stage_statuses.get(stage, "pending")
            dot_cls = STAGE_DOT_CLASSES.get(status, "dot-pending")
            label_cls = STAGE_LABEL_CLASSES.get(status, "")
            label_text = stage.replace("_", " ").title()

            with ui.element("div").classes("stage-item"):
                ui.element("div").classes(f"stage-dot {dot_cls}")
                ui.label(label_text).classes(f"stage-label {label_cls}")
