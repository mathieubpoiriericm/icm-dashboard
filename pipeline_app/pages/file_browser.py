"""File Browser page — directory tree and content viewer."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from nicegui import ui

from pipeline_app.components.file_content import (
    SUPPORTED_EXTENSIONS,
    render_file_content,
)
from pipeline_app.runner import resolve_project_root


def _is_within(path: Path, anchor: Path) -> bool:
    """True iff ``path`` resolves inside ``anchor``.

    ``anchor`` is expected to already be resolved. ``OSError`` covers
    the case where ``path.resolve()`` fails on a missing or unreadable
    intermediate component.
    """
    try:
        path.resolve().relative_to(anchor)
    except OSError, ValueError:
        return False
    return True


def _scan_directory(
    root: Path,
    base: Path,
    resolved_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Recursively scan a directory and return ui.tree-compatible nodes.

    Args:
        root: The project root (trust anchor for path validation).
        base: The directory to scan.
        resolved_root: Pre-resolved root path (avoids repeated resolve calls).

    Returns:
        List of tree node dicts with id, label, children keys.
    """
    if resolved_root is None:
        resolved_root = root.resolve()

    nodes: list[dict[str, Any]] = []

    try:
        entries = sorted(
            base.iterdir(),
            key=lambda p: (not p.is_dir(), p.name),
        )
    except PermissionError:
        return nodes

    for entry in entries:
        if entry.is_symlink():
            continue

        if not _is_within(entry, resolved_root):
            continue

        if entry.is_dir():
            children = _scan_directory(root, entry, resolved_root)
            if children:  # Exclude empty directories
                nodes.append(
                    {
                        "id": str(entry),
                        "label": entry.name,
                        "children": children,
                    }
                )
        elif entry.is_file():
            if entry.suffix.lower() in SUPPORTED_EXTENSIONS:
                nodes.append(
                    {
                        "id": str(entry),
                        "label": entry.name,
                        "children": [],
                    }
                )

    return nodes


def create_file_browser_page(project_root: str) -> None:
    """Render the File Browser page."""
    ui.label("File Browser").classes("page-title")

    root = resolve_project_root(project_root)
    logs_dir = root / "logs"
    # Resolve once so security checks can compare without re-resolving,
    # and so a non-existent logs/ still gets a stable absolute path.
    resolved_logs = logs_dir.resolve()

    if not project_root:
        ui.label(
            f"Project root is not configured; browsing logs/ relative to {root}."
        ).classes("text-warning q-mb-sm")

    selected_path: list[Path] = []
    content_container: list[ui.element] = []
    tree_container: list[ui.element] = []

    def _build_tree_nodes() -> list[dict[str, Any]]:
        if not logs_dir.exists():
            return []
        return _scan_directory(root, logs_dir)

    async def _on_file_select(e) -> None:
        file_path_str = e.value if hasattr(e, "value") else str(e)
        # Quasar QTree emits None (and possibly "") on deselect — clear
        # the stored selection so a stale path isn't reused by
        # "Open in System App".
        if not file_path_str:
            selected_path.clear()
            return
        path = Path(file_path_str)

        # The tree's node IDs come back through the WebSocket; treat them as
        # untrusted and confine reads to logs/ regardless of what was sent.
        if not _is_within(path, resolved_logs):
            ui.notify("Access denied: path outside logs/", color="negative")
            return

        if not path.is_file():
            return

        selected_path.clear()
        selected_path.append(path)

        if content_container:
            await render_file_content(path, content_container[0])

    def _refresh_tree() -> None:
        if tree_container:
            tree_container[0].clear()
            nodes = _build_tree_nodes()
            with tree_container[0]:
                if nodes:
                    ui.tree(
                        nodes,
                        label_key="label",
                        node_key="id",
                        on_select=_on_file_select,
                    ).classes("w-full")
                else:
                    ui.label(f"No supported files in {logs_dir}").classes("text-muted")

    def _open_in_system_app() -> None:
        if not selected_path:
            ui.notify("No file selected", color="warning")
            return
        path = selected_path[0]

        if not _is_within(path, resolved_logs):
            ui.notify("Access denied", color="negative")
            return

        if not path.exists():
            ui.notify("File no longer exists", color="warning")
            return

        # Use list form (no shell=True) to prevent injection
        if sys.platform == "darwin":
            opener = "open"
        elif sys.platform.startswith("linux"):
            opener = "xdg-open"
        else:
            ui.notify("Open not supported on this platform", color="warning")
            return

        try:
            # start_new_session + DEVNULL + close_fds prevents the opener
            # child (and any reparented grandchildren from xdg-open) from
            # inheriting NiceGUI's fds and stdio.
            subprocess.Popen(
                [opener, str(path)],
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        except FileNotFoundError:
            ui.notify(f"Could not open file: {opener} not found", color="negative")
        except OSError as e:
            ui.notify(f"Error opening file: {e}", color="negative")

    with ui.row().classes("q-mb-sm gap-sm"):
        ui.button(
            "Refresh",
            on_click=_refresh_tree,
            icon="refresh",
        ).props("outline size=sm")
        ui.button(
            "Open in System App",
            on_click=_open_in_system_app,
            icon="open_in_new",
        ).props("flat size=sm").classes("theme-btn-ghost")

    with ui.splitter(value=30).classes("w-full") as splitter:
        with (
            splitter.before,
            ui.card().classes("w-full h-full q-pa-sm theme-card") as tc,
        ):
            tree_container.append(tc)
            nodes = _build_tree_nodes()
            if nodes:
                ui.tree(
                    nodes,
                    label_key="label",
                    node_key="id",
                    on_select=_on_file_select,
                ).classes("w-full")
            else:
                ui.label(f"No supported files found in {logs_dir}").classes(
                    "text-muted"
                )

        with (
            splitter.after,
            ui.card().classes("w-full h-full q-pa-sm overflow-auto theme-card") as cc,
        ):
            content_container.append(cc)
            ui.label("Select a file to view its contents.").classes("text-muted")
