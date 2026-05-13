"""Shared helpers for pipeline_app_hpc page modules."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from nicegui import context

from pipeline_app_hpc.config import HpcAppConfig

if TYPE_CHECKING:
    from pipeline_app.components.execution_panel import ExecutionPanel


def get_project_anchor(config: HpcAppConfig) -> Path | None:
    """Return config.project_root as a resolved anchor, or None if unset."""
    if not config.project_root:
        return None
    with suppress(OSError, RuntimeError):
        return Path(config.project_root).expanduser().resolve()
    return None


class _RunnerWithListeners(Protocol):
    def add_listener(self, **kw: Any) -> Callable[[], None]: ...


def wire_runner_to_panel(
    runner: _RunnerWithListeners,
    panel: ExecutionPanel,
    refresh_tracker: Callable[[], None],
) -> None:
    """Forward stdout/stderr lines and stage updates from runner to panel.

    Registers a listener bundle that reads the live subprocess stream into
    the page's panel widgets, and disposes of it on client disconnect.
    """

    def _on_stdout(line: str) -> None:
        with suppress(RuntimeError):
            panel.append_stdout(line)

    def _on_stderr(line: str) -> None:
        with suppress(RuntimeError):
            panel.append_stderr(line)

    def _on_stage(_stage: str) -> None:
        with suppress(RuntimeError):
            refresh_tracker()

    dispose = runner.add_listener(
        on_stdout=_on_stdout,
        on_stderr=_on_stderr,
        on_stage=_on_stage,
    )
    # context.client raises outside a live request context (e.g. headless
    # tests); suppress the lookup, not an ImportError.
    with suppress(RuntimeError, AttributeError):
        context.client.on_disconnect(dispose)
