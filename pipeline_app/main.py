"""cSVD Pipeline App — NiceGUI entry point."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
from pathlib import Path

# Ensure the project root is on sys.path so `pipeline_app` is importable
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from nicegui import app, ui  # noqa: E402

from pipeline_app.config import (  # noqa: E402
    load_config,
    load_env_secrets,
    load_tuning_config,
)
from pipeline_app.pages.configure_run import create_configure_run_page  # noqa: E402
from pipeline_app.pages.file_browser import create_file_browser_page  # noqa: E402
from pipeline_app.pages.results_viewer import create_results_viewer_page  # noqa: E402
from pipeline_app.pages.run_history import create_run_history_page  # noqa: E402
from pipeline_app.pages.tuning import create_tuning_page  # noqa: E402
from pipeline_app.pages.tuning_history import create_tuning_history_page  # noqa: E402
from pipeline_app.runner import SubprocessLock, TuningRunner  # noqa: E402
from pipeline_app.theme import apply_theme  # noqa: E402

logger = logging.getLogger(__name__)

# Bound the shutdown cleanup so Ctrl+C doesn't hang behind a stubborn
# subprocess. SubprocessLock.cancel()'s worst case is SIGTERM wait (5s) +
# SIGKILL wait (5s) ≈ 10s; give ourselves a little headroom above that.
SHUTDOWN_TIMEOUT_SECONDS: float = 12.0


def build_shutdown_handler(
    lock: SubprocessLock,
    tuning_runner: TuningRunner,
    timeout: float = SHUTDOWN_TIMEOUT_SECONDS,
):
    """Build the app.on_shutdown handler for graceful subprocess cleanup.

    Exposed at module scope so tests can exercise it without booting NiceGUI.
    Bounded with wait_for: if the subprocess ignores SIGKILL (rare), log a
    warning and let the app exit — orphaning to PID 1 beats freezing forever.
    """

    async def _graceful_shutdown() -> None:
        async def _inner() -> None:
            # tuning_runner.cancel() already calls lock.cancel() internally;
            # the second lock.cancel() is a safety no-op for cases where a
            # plain PipelineRunner.run was active outside any tuning experiment.
            with contextlib.suppress(Exception):
                await tuning_runner.cancel()
            with contextlib.suppress(Exception):
                await lock.cancel()

        try:
            await asyncio.wait_for(_inner(), timeout=timeout)
        except TimeoutError:
            logger.warning(
                "Shutdown timeout after %.1fs: subprocess may orphan to PID 1",
                timeout,
            )

    return _graceful_shutdown


async def _cancel_any(tuning_runner: TuningRunner) -> None:
    """Cancel whatever is running. Safe when nothing is active."""
    await tuning_runner.cancel()


def create_sidebar(tuning_runner: TuningRunner) -> ui.left_drawer:
    """Build the left drawer sidebar with navigation links."""
    with ui.left_drawer(value=True).classes("column q-pa-md") as drawer:
        ui.label("Pipeline").classes("nav-section-label q-mb-xs")
        ui.button(
            "Configure & Run",
            on_click=lambda: ui.navigate.to("/"),
            icon="settings",
        ).props("flat").classes("w-full nav-item")
        ui.button(
            "Run History",
            on_click=lambda: ui.navigate.to("/history"),
            icon="history",
        ).props("flat").classes("w-full nav-item")

        ui.separator().classes("nav-separator")

        ui.label("Tuning").classes("nav-section-label q-mb-xs")
        ui.button(
            "Tuning",
            on_click=lambda: ui.navigate.to("/tuning"),
            icon="tune",
        ).props("flat").classes("w-full nav-item")
        ui.button(
            "Tuning History",
            on_click=lambda: ui.navigate.to("/tuning/history"),
            icon="analytics",
        ).props("flat").classes("w-full nav-item")

        ui.separator().classes("nav-separator")

        ui.label("Results").classes("nav-section-label q-mb-xs")
        ui.button(
            "File Browser",
            on_click=lambda: ui.navigate.to("/files"),
            icon="folder_open",
        ).props("flat").classes("w-full nav-item")

        ui.space()

        cancel_btn = ui.button(
            "Cancel",
            on_click=lambda: _cancel_any(tuning_runner),
            icon="cancel",
            color="negative",
        ).classes("w-full theme-btn-primary")
        # Covers tuning inter-stage waits, when the lock is momentarily
        # released between stages but the experiment is still live.
        cancel_btn.bind_visibility_from(tuning_runner, "any_running")

    return drawer


def create_header(drawer: ui.left_drawer) -> None:
    """Build the top header bar with hamburger toggle and app title."""
    with ui.header().classes("app-header"):
        ui.button(
            icon="menu",
            on_click=drawer.toggle,
        ).props("flat round size=sm")
        ui.label("cSVD Pipeline UI").classes("app-header-title")


def setup_pages(
    lock: SubprocessLock,
    tuning_runner: TuningRunner,
) -> None:
    """Register all @ui.page routes.

    Config and secrets are loaded inside each page handler so two browser
    tabs don't share a mutable config object via NiceGUI's two-way binding —
    edits in one tab would otherwise leak into the other and into the
    pipeline subprocess at run time.
    """

    @ui.page("/")
    def configure_run_page() -> None:
        config = load_config()
        secrets = load_env_secrets(config.project_root)
        drawer = create_sidebar(tuning_runner)
        create_header(drawer)
        create_configure_run_page(lock, config, secrets)

    @ui.page("/history")
    def run_history_page() -> None:
        drawer = create_sidebar(tuning_runner)
        create_header(drawer)
        create_run_history_page()

    @ui.page("/results/{report_id}")
    def results_viewer_page(report_id: str) -> None:
        config = load_config()
        drawer = create_sidebar(tuning_runner)
        create_header(drawer)
        create_results_viewer_page(report_id, config.project_root)

    @ui.page("/tuning")
    def tuning_page() -> None:
        config = load_config()
        tuning_config = load_tuning_config()
        drawer = create_sidebar(tuning_runner)
        create_header(drawer)
        create_tuning_page(lock, config, tuning_config, tuning_runner)

    @ui.page("/tuning/history")
    def tuning_history_page() -> None:
        config = load_config()
        drawer = create_sidebar(tuning_runner)
        create_header(drawer)
        create_tuning_history_page(config.project_root)

    @ui.page("/files")
    def file_browser_page() -> None:
        config = load_config()
        drawer = create_sidebar(tuning_runner)
        create_header(drawer)
        create_file_browser_page(config.project_root)


def main() -> None:
    """Create app state, register routes, and start NiceGUI."""
    lock = SubprocessLock()
    tuning_runner = TuningRunner(lock)

    setup_pages(lock, tuning_runner)

    # Register before ui.run() so NiceGUI wires it into the lifecycle.
    # Without this, start_new_session=True children survive Ctrl+C and
    # reparent to PID 1.
    app.on_shutdown(build_shutdown_handler(lock, tuning_runner))

    apply_theme()
    ui.run(
        title="cSVD Pipeline UI",
        host="127.0.0.1",
        port=8080,
        reload=False,
        dark=True,
    )


if __name__ == "__main__":
    main()
