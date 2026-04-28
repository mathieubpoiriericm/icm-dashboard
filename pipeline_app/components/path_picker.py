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
        raw_current = Path(current_value).expanduser()
        current_candidates: list[Path] = []
        if anchor is not None and not raw_current.is_absolute():
            current_candidates.append(anchor / raw_current)
        current_candidates.append(raw_current)

        for candidate in current_candidates:
            try:
                resolved = candidate.resolve()
            except OSError, RuntimeError:
                continue
            if resolved.is_dir():
                candidates.append(resolved)
                break
            if resolved.is_file():
                candidates.append(resolved.parent)
                break

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
    picker = _PathPickerDialog(
        mode=mode,
        anchor=anchor,
        current_value=current_value,
        fallback_start=fallback_start,
        extensions=extensions,
        allow_directories_as_files=allow_directories_as_files,
        symlinks=symlinks,
        title=title,
        save_as=save_as,
        default_filename=default_filename,
    )
    return await picker.open()


class _PathPickerDialog:
    """Stateful implementation behind the public ``pick_path`` coroutine."""

    def __init__(
        self,
        *,
        mode: PickerMode,
        anchor: Path | None,
        current_value: str,
        fallback_start: Path | None,
        extensions: frozenset[str] | None,
        allow_directories_as_files: bool,
        symlinks: SymlinkPolicy,
        title: str,
        save_as: bool,
        default_filename: str,
    ) -> None:
        self.mode = mode
        self.anchor = anchor
        self.current_value = current_value
        self.fallback_start = fallback_start
        self.extensions = extensions
        self.allow_directories_as_files = allow_directories_as_files
        self.symlinks = symlinks
        self.title = title
        self.save_as = save_as
        self.default_filename = default_filename

        self.resolved_anchor: Path | None = None
        self.current_dir: Path = Path.home()
        self.selected_file: Path | None = None
        self.dialog: ui.dialog | None = None
        self.bc_row: ui.row | None = None
        self.entries_col: ui.column | None = None
        self.filename_input: ui.input | None = None
        self.selected_label: ui.label | None = None

    async def open(self) -> str | None:
        # Resolve synchronous filesystem calls in a worker thread so a stalled
        # NFS/FUSE mount or a large symlink chain doesn't block the event loop.
        self.resolved_anchor = (
            await asyncio.to_thread(self.anchor.resolve)
            if self.anchor is not None
            else None
        )
        self.current_dir = await asyncio.to_thread(
            resolve_start_dir,
            self.current_value,
            self.fallback_start,
            self.resolved_anchor,
        )

        with ui.dialog() as dialog, ui.card().classes(
            "theme-card q-pa-md path-picker"
        ):
            self.dialog = dialog
            self._build_dialog()

        await self._refresh_entries()
        result = await dialog
        with suppress(RuntimeError):
            dialog.delete()
        return result

    def _build_dialog(self) -> None:
        ui.label(self.title).classes("section-header")
        self.bc_row = ui.row().classes("items-center wrap path-picker-breadcrumbs")
        self.entries_col = ui.column().classes("path-picker-entries")

        if self.save_as:
            with ui.row().classes("w-full q-mt-sm"):
                self.filename_input = ui.input(
                    label="New file name",
                    value=self.default_filename,
                ).classes("w-full path-picker-filename-input")

        self.selected_label = ui.label("").classes("path-picker-caption")
        with ui.row().classes("w-full justify-end path-picker-actions"):
            ui.button(
                "Cancel",
                on_click=lambda: self._submit(None),
            ).props("flat").classes("btn-ghost")
            if self.mode == "file" and self.allow_directories_as_files:
                ui.button(
                    "Select current folder",
                    on_click=self._select_current_folder,
                ).props("outline").classes("btn-secondary")
            select_btn = ui.button("Select").props("unelevated").classes("btn-primary")
        select_btn.on_click(self._select_current)
        self._refresh_breadcrumb()
        self._refresh_selected_label()

    def _submit(self, value: str | None) -> None:
        assert self.dialog is not None
        self.dialog.submit(value)

    def _select_current_folder(self) -> None:
        if self._outside_anchor(self.current_dir):
            ui.notify("Outside allowed folder", color="warning")
            return
        self._submit(str(self.current_dir))

    def _select_current(self) -> None:
        result = _compute_selection(
            mode=self.mode,
            current_dir=self.current_dir,
            selected_file=self.selected_file,
            anchor=self.resolved_anchor,
            save_as=self.save_as,
            filename=(
                self.filename_input.value if self.filename_input is not None else ""
            ),
        )
        if result is None:
            ui.notify("No valid selection", color="warning")
            return
        self._submit(str(result))

    async def _navigate_to(self, new_dir: Path) -> None:
        if self._outside_anchor(new_dir):
            ui.notify("Outside allowed folder", color="warning")
            return
        self.current_dir = await asyncio.to_thread(new_dir.resolve)
        self.selected_file = None
        self._refresh_breadcrumb()
        await self._refresh_entries()
        self._refresh_selected_label()

    async def _select_file(self, path: Path) -> None:
        if self._outside_anchor(path):
            ui.notify("Outside allowed folder", color="warning")
            return
        self.selected_file = path
        await self._refresh_entries()
        self._refresh_selected_label()

    def _outside_anchor(self, path: Path) -> bool:
        return self.resolved_anchor is not None and not is_within(
            path,
            self.resolved_anchor,
        )

    def _refresh_breadcrumb(self) -> None:
        assert self.bc_row is not None
        self.bc_row.clear()
        segments = _breadcrumb_segments(self.current_dir, self.resolved_anchor)
        last_idx = len(segments) - 1
        with self.bc_row:
            ui.icon("folder").classes("text-primary")
            for idx, segment in enumerate(segments):
                label = segment.name or str(segment)
                if idx == last_idx:
                    ui.label(label).classes("path-picker-breadcrumb-current")
                else:
                    ui.button(
                        label,
                        on_click=lambda _e=None, s=segment: asyncio.create_task(
                            self._navigate_to(s)
                        ),
                    ).props("flat dense").classes("btn-ghost")
                    ui.label("/").classes("path-picker-breadcrumb-sep")

    async def _refresh_entries(self) -> None:
        assert self.entries_col is not None
        items = await asyncio.to_thread(
            list_directory,
            self.current_dir,
            extensions=self.extensions,
            symlinks=self.symlinks,
        )
        selected = self.selected_file
        self.entries_col.clear()
        with self.entries_col:
            if not items:
                ui.label("(empty)").classes("path-picker-empty")
                return
            for item in items:
                self._render_entry(item, selected)

    def _render_entry(self, item, selected: Path | None) -> None:
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
                asyncio.create_task(self._navigate_to(i.path))
            elif self.mode == "file" and i.matches_filter:
                asyncio.create_task(self._select_file(i.path))

        with ui.row().classes(base_cls).on("click", _on_click):
            ui.icon(icon)
            ui.label(item.path.name)

    def _refresh_selected_label(self) -> None:
        assert self.selected_label is not None
        if self.save_as:
            self.selected_label.text = f"Save in: {self.current_dir}"
        elif self.mode == "directory":
            self.selected_label.text = f"Selected folder: {self.current_dir}"
        elif self.selected_file is not None:
            self.selected_label.text = f"Selected: {self.selected_file}"
        else:
            self.selected_label.text = "(no file selected)"


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
