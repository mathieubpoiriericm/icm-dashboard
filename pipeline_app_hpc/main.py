"""cSVD HPC Pipeline App — NiceGUI entry point on port 8081."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
from collections.abc import Awaitable, Callable
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

# Ensure project root is on sys.path so `pipeline_app_hpc` is importable
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from nicegui import app, context, ui  # noqa: E402

from pipeline_app_hpc.config import (  # noqa: E402
    load_config,
    load_tuning_config,
)
from pipeline_app_hpc.hpc.lifecycle import (  # noqa: E402
    VllmServer,
    VllmServerSnapshot,
    VllmServerState,
)
from pipeline_app_hpc.hpc.ssh import SshControlMaster  # noqa: E402
from pipeline_app_hpc.pages.configure_run import create_configure_run_page  # noqa: E402
from pipeline_app_hpc.pages.file_browser import create_file_browser_page  # noqa: E402
from pipeline_app_hpc.pages.results_viewer import create_results_viewer_page  # noqa: E402
from pipeline_app_hpc.pages.run_history import create_run_history_page  # noqa: E402
from pipeline_app_hpc.pages.tuning import create_tuning_page  # noqa: E402
from pipeline_app_hpc.pages.tuning_history import create_tuning_history_page  # noqa: E402
from pipeline_app_hpc.runner import (  # noqa: E402
    PipelineRunner,
    SubprocessLock,
    TuningRunner,
)
from pipeline_app_hpc.theme import apply_theme  # noqa: E402

logger = logging.getLogger(__name__)

# Bound the shutdown cleanup so Ctrl+C doesn't hang behind a stubborn
# subprocess. SubprocessLock.cancel()'s worst case is SIGTERM wait (5s) +
# SIGKILL wait (5s) ≈ 10s; give ourselves a little headroom above that.
SHUTDOWN_TIMEOUT_SECONDS: float = 12.0


def build_shutdown_handler(
    lock: SubprocessLock,
    tuning_runner: TuningRunner,
    vllm_server: VllmServer,
    ssh_master: SshControlMaster,
    timeout: float = SHUTDOWN_TIMEOUT_SECONDS,
) -> Callable[[], object]:
    """Build the app.on_shutdown handler for graceful cleanup.

    Exposed at module scope so tests can exercise it without booting NiceGUI.
    Bounded with wait_for: if any step hangs, log a warning and let the app
    exit rather than freezing forever.

    Shutdown order:
    1. tuning_runner.cancel() ‖ vllm_server.stop() — independent SIGTERM round-trips
    2. ssh_master.close() — last, since the others use the SSH master
    """
    # `lock` is only kept on the signature for tests/back-compat; the actual
    # cancellation is reached through tuning_runner.cancel(), which calls
    # lock.cancel() internally. Adding a second lock.cancel() here would be
    # a no-op for the active path and risks masking ordering bugs later.
    del lock

    async def _shutdown() -> None:
        async def _inner() -> None:
            tasks: list[Awaitable[object]] = [tuning_runner.cancel()]
            if vllm_server.snapshot.state != VllmServerState.IDLE:
                tasks.append(vllm_server.stop())
            await asyncio.gather(*tasks, return_exceptions=True)
            with contextlib.suppress(Exception):
                await ssh_master.close()

        try:
            await asyncio.wait_for(_inner(), timeout=timeout)
        except TimeoutError:
            logger.warning(
                "Shutdown timeout after %.1fs: subprocess may orphan to PID 1",
                timeout,
            )

    return _shutdown


def _render_vllm_status_chip(vllm_server: VllmServer) -> None:
    """Render the header's vLLM state chip, kept in sync via subscriber."""
    snap0 = vllm_server.snapshot
    state0 = snap0.state.value
    with ui.row().classes(
        f"vllm-status-chip vllm-state-{state0} items-center no-wrap"
    ) as chip:
        label = ui.label(f"vLLM: {state0.title()}")

    def _refresh(snap: VllmServerSnapshot) -> None:
        state = snap.state.value
        with contextlib.suppress(RuntimeError):
            label.set_text(f"vLLM: {state.title()}")
            css = f"vllm-status-chip vllm-state-{state} items-center no-wrap"
            chip.classes(replace=css)

    # Each page-render registers a fresh subscriber that closes over this
    # client's chip elements; tie its lifetime to the client connection so
    # stale callbacks aren't invoked against torn-down DOM.
    dispose = vllm_server.subscribe(_refresh)
    with contextlib.suppress(RuntimeError, AttributeError):
        context.client.on_disconnect(dispose)


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

        cancel_btn = (
            ui.button(
                "Cancel",
                on_click=tuning_runner.cancel,
                icon="cancel",
            )
            .props("unelevated")
            .classes("w-full btn-warning")
        )
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
    vllm_server: VllmServer,
    *,
    current_path: str = "",
    trailing: str | None = None,
) -> None:
    """Build the top header bar with menu, breadcrumbs, title, and status chips."""
    with ui.header().props("elevated").classes("app-header no-wrap"):
        ui.button(
            icon="menu",
            on_click=drawer.toggle,
        ).props("flat round size=sm").classes("btn-icon")
        _render_breadcrumbs(_breadcrumbs_for(current_path, trailing=trailing))
        _render_run_status_chip(tuning_runner)
        _render_vllm_status_chip(vllm_server)
        ui.label("cSVD HPC Pipeline UI").classes("app-header-title")


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
    pipeline_runner: PipelineRunner,
    vllm_server: VllmServer,
) -> None:
    """Register all @ui.page routes.

    Config and secrets are loaded inside each page handler so two browser
    tabs don't share a mutable config object via NiceGUI's two-way binding —
    edits in one tab would otherwise leak into the other and into the
    pipeline subprocess at run time.
    """

    def _render_page(
        current_path: str,
        body: Callable[[], None],
        *,
        trailing: str | None = None,
    ) -> None:
        drawer = create_sidebar(tuning_runner, current_path=current_path)
        create_header(
            drawer,
            tuning_runner,
            vllm_server,
            current_path=current_path,
            trailing=trailing,
        )
        with wrap_page_root():
            body()
        create_footer()

    @ui.page("/")
    def configure_run_page() -> None:
        config = load_config()
        _render_page(
            "/",
            lambda: create_configure_run_page(
                lock,
                config,
                pipeline_runner,
                vllm_server,
            ),
        )

    @ui.page("/history")
    def run_history_page() -> None:
        _render_page("/history", create_run_history_page)

    @ui.page("/results/{report_id}")
    def results_viewer_page(report_id: str) -> None:
        config = load_config()
        # Short report id in the trailing crumb keeps the header from
        # overflowing when the id is a long timestamped filename.
        trailing = f"Report · {report_id[:20]}"
        _render_page(
            "/results",
            lambda: create_results_viewer_page(report_id, config.project_root),
            trailing=trailing,
        )

    @ui.page("/tuning")
    def tuning_page() -> None:
        config = load_config()
        tuning_config = load_tuning_config()
        _render_page(
            "/tuning",
            lambda: create_tuning_page(
                lock, config, tuning_config, tuning_runner, vllm_server
            ),
        )

    @ui.page("/tuning/history")
    def tuning_history_page() -> None:
        config = load_config()
        _render_page(
            "/tuning/history",
            lambda: create_tuning_history_page(config.project_root),
        )

    @ui.page("/files")
    def file_browser_page() -> None:
        config = load_config()
        _render_page("/files", lambda: create_file_browser_page(config.project_root))


def main() -> None:
    """Create app state, register routes, and start NiceGUI on port 8081."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config = load_config()
    lock = SubprocessLock()
    socket_path = Path(config.ssh_socket_path) if config.ssh_socket_path else None
    ssh_master = SshControlMaster(alias=config.ssh_alias, socket_path=socket_path)
    vllm_server = VllmServer(ssh=ssh_master, config=config)
    tuning_runner = TuningRunner(lock, vllm_server)
    pipeline_runner = PipelineRunner(lock, vllm_server)

    setup_pages(lock, tuning_runner, pipeline_runner, vllm_server)

    # Register before ui.run() so NiceGUI wires it into the lifecycle.
    # Without this, start_new_session=True children survive Ctrl+C and
    # reparent to PID 1.
    app.on_shutdown(
        build_shutdown_handler(lock, tuning_runner, vllm_server, ssh_master)
    )

    apply_theme()
    ui.run(
        title="cSVD HPC Pipeline UI",
        host="127.0.0.1",
        port=8081,
        reload=False,
        dark=False,
    )


if __name__ == "__main__":
    main()
