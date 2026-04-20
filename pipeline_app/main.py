"""cSVD Pipeline App — NiceGUI entry point."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
from importlib.metadata import PackageNotFoundError, version
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


# Sidebar sections: each entry is (heading, heading_icon, [(path, label, icon), ...]).
_NAV_SECTIONS: tuple[tuple[str, str, tuple[tuple[str, str, str], ...]], ...] = (
    (
        "Pipeline",
        "play_circle_outline",
        (
            ("/", "Configure & Run", "settings"),
            ("/history", "Run History", "history"),
        ),
    ),
    (
        "Tuning",
        "tune",
        (
            ("/tuning", "Tuning", "tune"),
            ("/tuning/history", "Tuning History", "analytics"),
        ),
    ),
    (
        "Results",
        "folder_open",
        (("/files", "File Browser", "folder_open"),),
    ),
)

# Path → breadcrumb chain of (label, link_or_None). A link of None marks
# the current page (rendered non-clickable).
_BREADCRUMBS: dict[str, tuple[tuple[str, str | None], ...]] = {
    "/": (("Configure & Run", None),),
    "/history": (("Run History", None),),
    "/tuning": (("Tuning", None),),
    "/tuning/history": (
        ("Tuning", "/tuning"),
        ("History", None),
    ),
    "/files": (("File Browser", None),),
    "/results": (
        ("Run History", "/history"),
        ("Report", None),
    ),
}


def _breadcrumbs_for(
    path: str,
    *,
    trailing: str | None = None,
) -> tuple[tuple[str, str | None], ...]:
    """Return the breadcrumb chain for a semantic path.

    When ``trailing`` is provided, it replaces or appends to the final
    non-clickable crumb — useful for parametric paths like ``/results``
    where the tail label is derived at render time.
    """
    chain = _BREADCRUMBS.get(path, ())
    if not trailing:
        return chain
    if chain and chain[-1][1] is None:
        return chain[:-1] + ((trailing, None),)
    return chain + ((trailing, None),)


def create_sidebar(
    tuning_runner: TuningRunner,
    current_path: str = "",
) -> ui.left_drawer:
    """Build the left drawer sidebar with navigation links.

    Args:
        tuning_runner: Used to bind the Cancel button's visibility.
        current_path: Route of the active page; the matching nav button
            gets the ``nav-active`` class so users always know where they
            are.
    """
    with ui.left_drawer(value=True).classes("column q-pa-md") as drawer:
        for s_i, (heading, heading_icon, items) in enumerate(_NAV_SECTIONS):
            if s_i > 0:
                ui.separator().classes("nav-separator")
            with ui.row().classes("items-center q-mb-xs no-wrap"):
                ui.icon(heading_icon).classes("nav-section-icon")
                ui.label(heading).classes("nav-section-label")
            for path, label, icon in items:
                btn = (
                    ui.button(
                        label,
                        on_click=lambda p=path: ui.navigate.to(p),
                        icon=icon,
                    )
                    .props("flat")
                    .classes("w-full nav-item")
                )
                if path == current_path:
                    btn.classes("nav-active")

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


def _render_breadcrumbs(
    crumbs: tuple[tuple[str, str | None], ...],
) -> None:
    """Render a breadcrumb chain inside the header."""
    with ui.row().classes("app-breadcrumbs no-wrap items-center"):
        ui.label("Home").classes("app-breadcrumb-link").on(
            "click", lambda: ui.navigate.to("/")
        )
        for label, link in crumbs:
            ui.label("/").classes("app-breadcrumb-separator")
            if link:
                target = link
                ui.label(label).classes("app-breadcrumb-link").on(
                    "click", lambda _=None, t=target: ui.navigate.to(t)
                )
            else:
                ui.label(label).classes("app-breadcrumb-current")


def _render_run_status_chip(tuning_runner: TuningRunner) -> None:
    """Render the header's run-status chip, bound to tuning_runner.any_running."""
    with ui.row().classes("run-status-chip items-center no-wrap") as chip:
        ui.element("span").classes("run-status-chip-dot")
        ui.label("Running")
    chip.bind_visibility_from(tuning_runner, "any_running")


def create_header(
    drawer: ui.left_drawer,
    tuning_runner: TuningRunner,
    *,
    current_path: str = "",
    trailing: str | None = None,
) -> None:
    """Build the top header bar with menu, breadcrumbs, title, status chip."""
    with ui.header().props("elevated").classes("app-header no-wrap"):
        ui.button(
            icon="menu",
            on_click=drawer.toggle,
        ).props("flat round size=sm")
        _render_breadcrumbs(_breadcrumbs_for(current_path, trailing=trailing))
        _render_run_status_chip(tuning_runner)
        ui.label("cSVD Pipeline UI").classes("app-header-title")


try:
    _NICEGUI_VERSION: str = version("nicegui")
except PackageNotFoundError:
    _NICEGUI_VERSION = "?"


def create_footer() -> None:
    """Slim footer with brand on the left and version info on the right."""
    with ui.footer().props("elevated").classes("app-footer"):
        ui.label("cSVD · Paris Brain Institute · ICM").classes("text-muted")
        ui.label(f"NiceGUI {_NICEGUI_VERSION}")


def wrap_page_root() -> ui.element:
    """Wrapper that triggers the page fade-in-up on mount.

    Callers should use it as a context manager around page content:

    .. code-block:: python

        with wrap_page_root():
            create_configure_run_page(...)
    """
    return ui.element("div").classes("page-root w-full")


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
        drawer = create_sidebar(tuning_runner, current_path="/")
        create_header(drawer, tuning_runner, current_path="/")
        with wrap_page_root():
            create_configure_run_page(lock, config, secrets)
        create_footer()

    @ui.page("/history")
    def run_history_page() -> None:
        drawer = create_sidebar(tuning_runner, current_path="/history")
        create_header(drawer, tuning_runner, current_path="/history")
        with wrap_page_root():
            create_run_history_page()
        create_footer()

    @ui.page("/results/{report_id}")
    def results_viewer_page(report_id: str) -> None:
        config = load_config()
        drawer = create_sidebar(tuning_runner, current_path="/results")
        # Short report id in the trailing crumb keeps the header from
        # overflowing when the id is a long timestamped filename.
        trailing = f"Report · {report_id[:20]}"
        create_header(
            drawer,
            tuning_runner,
            current_path="/results",
            trailing=trailing,
        )
        with wrap_page_root():
            create_results_viewer_page(report_id, config.project_root)
        create_footer()

    @ui.page("/tuning")
    def tuning_page() -> None:
        config = load_config()
        tuning_config = load_tuning_config()
        drawer = create_sidebar(tuning_runner, current_path="/tuning")
        create_header(drawer, tuning_runner, current_path="/tuning")
        with wrap_page_root():
            create_tuning_page(lock, config, tuning_config, tuning_runner)
        create_footer()

    @ui.page("/tuning/history")
    def tuning_history_page() -> None:
        config = load_config()
        drawer = create_sidebar(tuning_runner, current_path="/tuning/history")
        create_header(
            drawer,
            tuning_runner,
            current_path="/tuning/history",
        )
        with wrap_page_root():
            create_tuning_history_page(config.project_root)
        create_footer()

    @ui.page("/files")
    def file_browser_page() -> None:
        config = load_config()
        drawer = create_sidebar(tuning_runner, current_path="/files")
        create_header(drawer, tuning_runner, current_path="/files")
        with wrap_page_root():
            create_file_browser_page(config.project_root)
        create_footer()


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
        dark=False,
    )


if __name__ == "__main__":
    main()
