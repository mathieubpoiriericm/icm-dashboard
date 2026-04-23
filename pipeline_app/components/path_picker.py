"""File/directory picker dialog for path-typed form fields."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path
from typing import Literal

from nicegui import ui

from pipeline_app.components.fs_nav import (
    SymlinkPolicy,
    is_within,
    list_directory,
)

PickerMode = Literal["file", "directory"]

_MAX_FILENAME_LEN = 255
_FORBIDDEN_FILENAME_CHARS = frozenset({"/", "\\", "\x00"})
_FORBIDDEN_FILENAMES = frozenset({".", ".."})


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def resolve_start_dir(
    current_value: str,
    fallback_start: Path | None,
    anchor: Path | None,
) -> Path:
    """Choose the initial directory for the picker tree.

    Rules, in order:
    1. ``current_value`` is a path to an existing directory → use it.
    2. ``current_value`` is a path to an existing file → use its parent.
    3. ``fallback_start`` if provided.
    4. ``anchor`` if provided.
    5. ``Path.home()``.

    Never returns ``Path("/")``: exposing the filesystem root as the
    initial view turns the picker into a read oracle even when no
    anchor is set. If all candidates resolve to ``/``, fall back to
    ``Path.home()``.
    """
    candidates: list[Path] = []

    if current_value:
        try:
            resolved = Path(current_value).expanduser().resolve()
        except OSError, RuntimeError:
            resolved = None
        if resolved is not None:
            if resolved.is_dir():
                candidates.append(resolved)
            elif resolved.is_file():
                candidates.append(resolved.parent)

    if fallback_start is not None:
        candidates.append(fallback_start)
    if anchor is not None:
        candidates.append(anchor)
    candidates.append(Path.home())

    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError, RuntimeError:
            continue
        if resolved.is_dir() and resolved != Path(resolved.anchor):
            return resolved

    return Path.home().resolve()


def is_valid_save_as_filename(name: str) -> bool:
    """Return True if ``name`` is safe to append to a chosen directory.

    Rejects empty strings, path separators, null bytes, ``.`` / ``..``,
    and names that exceed a conservative POSIX length cap.
    """
    stripped = name.strip()
    if not stripped:
        return False
    if stripped in _FORBIDDEN_FILENAMES:
        return False
    if len(stripped) > _MAX_FILENAME_LEN:
        return False
    return not any(c in _FORBIDDEN_FILENAME_CHARS for c in stripped)


def combine_save_as(
    directory: Path,
    filename: str,
    anchor: Path | None,
) -> Path | None:
    """Build the final save-as path and enforce the sandbox.

    Returns the joined path iff it passes ``is_within(anchor)`` (or
    anchor is unset). Returns ``None`` when the filename is invalid
    or the combined path escapes the anchor.
    """
    if not is_valid_save_as_filename(filename):
        return None
    combined = directory / filename.strip()
    if anchor is not None and not is_within(combined, anchor):
        return None
    return combined


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------


def _breadcrumb_segments(current: Path, anchor: Path | None) -> list[Path]:
    """Ancestor chain to render as breadcrumb buttons, anchor-aware.

    When an anchor is set, the chain stops at the anchor so users
    cannot navigate above their sandbox via breadcrumb clicks.
    """
    chain: list[Path] = [current]
    stop = anchor.resolve() if anchor is not None else None
    # Stop BEFORE appending the parent when the current tail is already
    # the sandbox anchor — otherwise we emit one segment above anchor,
    # which resolves outside the sandbox and surfaces as a bogus
    # "Outside allowed folder" notice when the user clicks it.
    while True:
        if stop is not None and chain[-1] == stop:
            break
        parent = chain[-1].parent
        if parent == chain[-1]:
            break
        chain.append(parent)
    return list(reversed(chain))


async def pick_path(
    *,
    mode: PickerMode,
    anchor: Path | None = None,
    current_value: str = "",
    fallback_start: Path | None = None,
    extensions: frozenset[str] | None = None,
    allow_directories_as_files: bool = False,
    symlinks: SymlinkPolicy = "reject",
    title: str = "Select a path",
    save_as: bool = False,
    default_filename: str = "",
) -> str | None:
    """Open a file/directory picker dialog.

    Args:
        mode: ``"file"`` to pick a file, ``"directory"`` to pick a directory.
        anchor: Sandbox root. Pre-resolved absolute path. ``None`` means
            no sandbox (python_path, project_root). Callers must validate
            the selection themselves in that case.
        current_value: Existing field value; used to preselect the start
            directory and (when the path exists) prefocus the entry.
        fallback_start: Directory to open when ``current_value`` is empty
            or a bare name like ``"python3"``.
        extensions: File suffixes (lowercased, with leading dot) that
            count as "matching". Non-matching files are still listed but
            rendered dimmed. ``None`` means every file matches.
        allow_directories_as_files: When ``True`` and ``mode="file"``,
            an extra "Select current folder" button commits the current
            directory — used for ``--local-pdfs`` which accepts either.
        symlinks: ``"preserve"`` keeps symlinks visible (required for
            venv ``python3.x``). Default is the secure ``"reject"``.
        title: Dialog title.
        save_as: Compose the final path from a user-typed filename plus
            the chosen directory. Used for ``progress_file``.
        default_filename: Prefill for the save-as filename input.

    Returns:
        The absolute selected path as a string, or ``None`` if cancelled.
    """
    # Resolve synchronous filesystem calls in a worker thread so a stalled
    # NFS/FUSE mount or a large symlink chain doesn't block the event loop
    # (and every other connected client) while the picker is opening.
    resolved_anchor = (
        await asyncio.to_thread(anchor.resolve) if anchor is not None else None
    )
    start_dir = await asyncio.to_thread(
        resolve_start_dir, current_value, fallback_start, resolved_anchor
    )
    current_dir_holder: list[Path] = [start_dir]
    selected_file_holder: list[Path | None] = [None]

    with ui.dialog() as dialog, ui.card().classes("theme-card q-pa-md path-picker"):
        ui.label(title).classes("section-header")

        # Empty at creation; _refresh_breadcrumb / _refresh_entries populate
        # them in place later. No `with` scope needed because nothing is
        # added inline.
        bc_row = ui.row().classes("items-center wrap path-picker-breadcrumbs")
        entries_col = ui.column().classes("path-picker-entries")

        filename_input: ui.input | None = None
        if save_as:
            with ui.row().classes("w-full q-mt-sm"):
                filename_input = ui.input(
                    label="New file name",
                    value=default_filename,
                ).classes("w-full path-picker-filename-input")

        selected_label = ui.label("").classes("path-picker-caption")

        with ui.row().classes("w-full justify-end path-picker-actions"):
            ui.button(
                "Cancel",
                on_click=lambda: dialog.submit(None),
            ).props("flat").classes("btn-ghost")

            def _on_select_folder_click() -> None:
                # Re-check the anchor at submit time so this path matches
                # the validation _on_select_click does for file selections.
                folder = current_dir_holder[0]
                if resolved_anchor is not None and not is_within(
                    folder, resolved_anchor
                ):
                    ui.notify("Outside allowed folder", color="warning")
                    return
                dialog.submit(str(folder))

            if mode == "file" and allow_directories_as_files:
                ui.button(
                    "Select current folder",
                    on_click=_on_select_folder_click,
                ).props("outline").classes("btn-secondary")
            select_btn = ui.button("Select").props("unelevated").classes("btn-primary")

        def _on_select_click() -> None:
            result = _compute_selection(
                mode=mode,
                current_dir=current_dir_holder[0],
                selected_file=selected_file_holder[0],
                anchor=resolved_anchor,
                save_as=save_as,
                filename=filename_input.value if filename_input is not None else "",
            )
            if result is None:
                ui.notify("No valid selection", color="warning")
                return
            dialog.submit(str(result))

        select_btn.on_click(_on_select_click)

        async def _navigate_to(new_dir: Path) -> None:
            if resolved_anchor is not None and not is_within(new_dir, resolved_anchor):
                ui.notify("Outside allowed folder", color="warning")
                return
            current_dir_holder[0] = await asyncio.to_thread(new_dir.resolve)
            selected_file_holder[0] = None
            _refresh_breadcrumb()
            await _refresh_entries()
            _refresh_selected_label()

        async def _select_file(path: Path) -> None:
            if resolved_anchor is not None and not is_within(path, resolved_anchor):
                ui.notify("Outside allowed folder", color="warning")
                return
            selected_file_holder[0] = path
            await _refresh_entries()
            _refresh_selected_label()

        def _refresh_breadcrumb() -> None:
            bc_row.clear()
            segments = _breadcrumb_segments(current_dir_holder[0], resolved_anchor)
            last_idx = len(segments) - 1
            with bc_row:
                ui.icon("folder").classes("text-primary")
                for idx, segment in enumerate(segments):
                    is_last = idx == last_idx
                    label = segment.name or str(segment)
                    if is_last:
                        ui.label(label).classes("path-picker-breadcrumb-current")
                    else:
                        ui.button(
                            label,
                            on_click=lambda _e=None, s=segment: asyncio.create_task(
                                _navigate_to(s)
                            ),
                        ).props("flat dense").classes("btn-ghost")
                        ui.label("/").classes("path-picker-breadcrumb-sep")

        async def _refresh_entries() -> None:
            items = await asyncio.to_thread(
                list_directory,
                current_dir_holder[0],
                extensions=extensions,
                symlinks=symlinks,
            )
            # Snapshot after the await so a concurrent _select_file update
            # is visible at render time (sampling pre-await drops the
            # highlight on the just-selected file).
            selected = selected_file_holder[0]
            entries_col.clear()
            with entries_col:
                if not items:
                    ui.label("(empty)").classes("path-picker-empty")
                    return
                for item in items:
                    _render_entry(item, selected)

        def _render_entry(item, selected: Path | None) -> None:
            # list_directory's item.path shares current_dir's resolution,
            # and _select_file stores paths drawn from the same listing —
            # so pointer-style equality works without an extra resolve().
            is_selected_file = (
                not item.is_dir and selected is not None and selected == item.path
            )
            icon = "folder" if item.is_dir else "description"
            base_cls = "path-picker-row w-full rounded-borders"
            if is_selected_file:
                base_cls += " path-picker-row-selected"
            elif not item.is_dir and not item.matches_filter:
                base_cls += " text-muted"

            def _on_click(i=item) -> None:
                if i.is_dir:
                    asyncio.create_task(_navigate_to(i.path))
                elif mode == "file" and i.matches_filter:
                    asyncio.create_task(_select_file(i.path))

            with ui.row().classes(base_cls).on("click", _on_click):
                ui.icon(icon)
                ui.label(item.path.name)

        def _refresh_selected_label() -> None:
            if save_as:
                selected_label.text = f"Save in: {current_dir_holder[0]}"
            elif mode == "directory":
                selected_label.text = f"Selected folder: {current_dir_holder[0]}"
            elif selected_file_holder[0] is not None:
                selected_label.text = f"Selected: {selected_file_holder[0]}"
            else:
                selected_label.text = "(no file selected)"

        _refresh_breadcrumb()
        _refresh_selected_label()

    await _refresh_entries()
    result = await dialog
    with suppress(RuntimeError):
        dialog.delete()
    return result


def _compute_selection(
    *,
    mode: PickerMode,
    current_dir: Path,
    selected_file: Path | None,
    anchor: Path | None,
    save_as: bool,
    filename: str,
) -> Path | None:
    """Apply mode-specific rules to derive the returned path.

    Separated from the dialog body so the logic is unit-testable
    without a NiceGUI client. Returns ``None`` when the current state
    does not yield a valid selection (e.g. file mode with nothing
    clicked, or save-as with an invalid filename).
    """
    if save_as:
        return combine_save_as(current_dir, filename, anchor)
    if mode == "directory":
        if anchor is not None and not is_within(current_dir, anchor):
            return None
        return current_dir
    if selected_file is None:
        return None
    if anchor is not None and not is_within(selected_file, anchor):
        return None
    return selected_file
