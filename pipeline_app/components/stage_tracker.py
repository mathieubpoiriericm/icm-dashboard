"""Reusable stage progress indicator component — vertical timeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from nicegui import ui

StageStatus = Literal["pending", "running", "completed", "failed", "skipped"]


@dataclass(slots=True, frozen=True)
class _StatusConfig:
    icon_name: str
    icon_cls: str
    label_cls: str = ""


# Keyed on str (not StageStatus) so runner-supplied statuses — which arrive
# as bare strings from a subprocess stdout marker — can be looked up without
# a narrowing cast. ``test_stage_tracker.test_covers_every_status`` asserts
# the keys still match the StageStatus Literal.
_STATUS_CONFIG: dict[str, _StatusConfig] = {
    "pending": _StatusConfig("radio_button_unchecked", "icon-pending"),
    "running": _StatusConfig("progress_activity", "icon-running", "label-running"),
    "completed": _StatusConfig("check_circle", "icon-completed", "label-completed"),
    "failed": _StatusConfig("cancel", "icon-failed", "label-failed"),
    "skipped": _StatusConfig("remove_circle_outline", "icon-skipped"),
}

_FALLBACK = _STATUS_CONFIG["pending"]


def format_duration(seconds: float) -> str:
    """Format a duration for the inline stage label (e.g. ``12.4s``, ``2m 03s``)."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, rem = divmod(int(seconds), 60)
    return f"{minutes}m {rem:02d}s"


def create_stage_tracker(
    stages: list[str],
    stage_statuses: dict[str, str],
    current_repeat: int = 0,
    total_repeats: int = 0,
    *,
    stage_durations: dict[str, float] | None = None,
) -> None:
    """Render a vertical timeline of stages with status icons.

    Args:
        stages: Ordered stage names.
        stage_statuses: Map of stage name → status
            (``pending``/``running``/``completed``/``failed``/``skipped``).
        current_repeat: 1-based index of the current repeat loop.
        total_repeats: Total repeat count (>1 enables the counter badge).
        stage_durations: Optional map of stage name → elapsed seconds.
            Completed stages with a duration render a mono time label.
    """
    if current_repeat > 0 and total_repeats > 1:
        ui.html(
            f'<span class="repeat-counter">'
            f"Repeat {current_repeat}/{total_repeats}"
            f"</span>"
        )

    durations = stage_durations or {}

    with ui.element("div").classes("stage-timeline"):
        for stage in stages:
            status = stage_statuses.get(stage, "pending")
            cfg = _STATUS_CONFIG.get(status, _FALLBACK)
            label_text = stage.replace("_", " ").title()
            duration = durations.get(stage)

            with ui.element("div").classes("stage-item"):
                ui.icon(cfg.icon_name).classes(f"stage-icon {cfg.icon_cls}")
                ui.label(label_text).classes(f"stage-label {cfg.label_cls}".rstrip())
                if duration is not None and status in ("completed", "failed"):
                    ui.label(format_duration(duration)).classes("stage-duration")
