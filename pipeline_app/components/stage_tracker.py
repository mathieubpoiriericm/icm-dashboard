"""Reusable stage progress indicator component — vertical timeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from nicegui import ui

StageStatus = Literal[
    "pending",
    "running",
    "completed",
    "failed",
    "skipped",
    "cancelled",
]


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
    "cancelled": _StatusConfig("block", "icon-cancelled", "label-cancelled"),
}

_FALLBACK = _STATUS_CONFIG["pending"]


def format_duration(seconds: float) -> str:
    """Format a duration for the inline stage label (e.g. ``12.4s``, ``2m 03s``)."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, rem = divmod(int(seconds), 60)
    return f"{minutes}m {rem:02d}s"


class StageTracker:
    """Stable-DOM timeline whose icons/labels/durations mutate in place.

    Re-renders via ``update(...)`` don't clear or rebuild any elements — they
    swap BindableProperty values on the existing ``q-icon`` / ``div`` nodes
    so stage transitions avoid the layout-flash of ``container.clear() +
    rebuild``.
    """

    def __init__(self, stages: list[str]) -> None:
        self._stages = list(stages)
        self._icons: dict[str, ui.icon] = {}
        self._labels: dict[str, ui.label] = {}
        self._duration_labels: dict[str, ui.label] = {}
        # Per-stage (status, duration) of the last apply — skip the DOM
        # mutations when nothing changed so frequent polls in the caller
        # don't trigger redundant ``classes(replace=...)`` updates.
        self._last_per_stage: dict[str, tuple[str, float | None]] = {}
        self._last_repeat: tuple[int, int] | None = None

        self._repeat_html = ui.html("")
        self._repeat_html.set_visibility(False)

        with ui.element("div").classes("stage-timeline"):
            for stage in stages:
                label_text = stage.replace("_", " ").title()
                with ui.element("div").classes("stage-item"):
                    self._icons[stage] = ui.icon(_FALLBACK.icon_name).classes(
                        f"stage-icon {_FALLBACK.icon_cls}"
                    )
                    self._labels[stage] = ui.label(label_text).classes("stage-label")
                    dur = ui.label("").classes("stage-duration")
                    dur.set_visibility(False)
                    self._duration_labels[stage] = dur

    def update(
        self,
        stage_statuses: dict[str, str],
        current_repeat: int = 0,
        total_repeats: int = 0,
        *,
        stage_durations: dict[str, float] | None = None,
    ) -> None:
        """Mutate the existing tracker DOM to reflect new state."""
        repeat_key = (current_repeat, total_repeats)
        if repeat_key != self._last_repeat:
            if current_repeat > 0 and total_repeats > 1:
                self._repeat_html.content = (
                    f'<span class="repeat-counter">'
                    f"Repeat {current_repeat}/{total_repeats}"
                    f"</span>"
                )
                self._repeat_html.set_visibility(True)
            else:
                self._repeat_html.set_visibility(False)
            self._last_repeat = repeat_key

        durations = stage_durations or {}
        for stage in self._stages:
            status = stage_statuses.get(stage, "pending")
            duration = durations.get(stage)
            key = (status, duration)
            if self._last_per_stage.get(stage) == key:
                continue
            self._last_per_stage[stage] = key

            cfg = _STATUS_CONFIG.get(status, _FALLBACK)
            icon = self._icons[stage]
            icon.name = cfg.icon_name
            icon.classes(replace=f"stage-icon {cfg.icon_cls}")

            label_cls = f"stage-label {cfg.label_cls}".rstrip()
            self._labels[stage].classes(replace=label_cls)

            dur_label = self._duration_labels[stage]
            if duration is not None and status in ("completed", "failed", "cancelled"):
                dur_label.text = format_duration(duration)
                dur_label.set_visibility(True)
            else:
                dur_label.set_visibility(False)


def create_stage_tracker(
    stages: list[str],
    stage_statuses: dict[str, str],
    current_repeat: int = 0,
    total_repeats: int = 0,
    *,
    stage_durations: dict[str, float] | None = None,
) -> StageTracker:
    """Render a vertical timeline of stages with status icons.

    Returns the tracker handle so the caller can keep it alive and call
    ``tracker.update(new_statuses, ...)`` to reflect state changes without a
    DOM rebuild.

    Args:
        stages: Ordered stage names.
        stage_statuses: Initial status map
            (``pending``/``running``/``completed``/``failed``/``skipped``).
        current_repeat: 1-based index of the current repeat loop.
        total_repeats: Total repeat count (>1 enables the counter badge).
        stage_durations: Optional map of stage name → elapsed seconds.
            Completed stages with a duration render a mono time label.
    """
    tracker = StageTracker(stages)
    tracker.update(
        stage_statuses,
        current_repeat=current_repeat,
        total_repeats=total_repeats,
        stage_durations=stage_durations,
    )
    return tracker
