"""Shared pieces for live execution panels."""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path

from nicegui import ui

from pipeline_app.components.log_viewer import (
    STDERR_CSS_CLASS,
    LogViewer,
    css_class_for,
    detect_severity,
)
from pipeline_app.components.stage_tracker import StageTracker, create_stage_tracker


def replay_log_lines(
    log_viewer: LogViewer,
    log_lines: Iterable[tuple[str, str]],
) -> None:
    """Replay buffered runner log lines into a ``LogViewer``."""
    replay: list[tuple[str, str]] = []
    for type_, line in log_lines:
        if type_ == "out":
            replay.append((line, css_class_for(detect_severity(line))))
        else:
            replay.append((f"[stderr] {line}", STDERR_CSS_CLASS))
    log_viewer.load_batch(replay)


class ExecutionPanel:
    """Owns the common tracker + log viewer DOM used by run pages."""

    def __init__(
        self,
        stages: list[str],
        stage_statuses: dict[str, str],
        *,
        current_repeat: int = 0,
        total_repeats: int = 0,
        stage_durations: dict[str, float] | None = None,
    ) -> None:
        with ui.card().classes("w-full q-pa-sm q-mb-sm theme-card-elevated"):
            self.stage_tracker: StageTracker = create_stage_tracker(
                stages,
                stage_statuses,
                current_repeat,
                total_repeats,
                stage_durations=stage_durations,
            )
        self.log_viewer = LogViewer()

    def refresh(
        self,
        stage_statuses: dict[str, str],
        current_repeat: int = 0,
        total_repeats: int = 0,
        *,
        stage_durations: dict[str, float] | None = None,
    ) -> None:
        """Update the tracker in-place, ignoring detached-client errors."""
        with suppress(RuntimeError):
            self.stage_tracker.update(
                stage_statuses,
                current_repeat,
                total_repeats,
                stage_durations=stage_durations,
            )

    def append_stdout(self, line: str) -> None:
        with suppress(RuntimeError):
            self.log_viewer.append(line)

    def append_stderr(self, line: str) -> None:
        with suppress(RuntimeError):
            self.log_viewer.append_stderr(line)

    def clear_log(self) -> None:
        with suppress(RuntimeError):
            self.log_viewer.clear()

    def replay(self, log_lines: Iterable[tuple[str, str]]) -> None:
        with suppress(RuntimeError):
            replay_log_lines(self.log_viewer, log_lines)


def render_output_links(
    container: ui.element,
    stage: str,
    output_files: list[Path],
) -> None:
    """Append tuning output file labels to the output container."""
    if not output_files:
        return
    with suppress(RuntimeError), container:
        for f in output_files:
            ui.label(f"[{stage}] {f.name}").style(
                "color: var(--theme-secondary);"
            )
