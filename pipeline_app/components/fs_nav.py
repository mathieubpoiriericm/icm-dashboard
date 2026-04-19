"""Filesystem navigation helpers shared by file browser and path picker."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pipeline_app.components.file_content import SUPPORTED_EXTENSIONS


@dataclass(slots=True, frozen=True)
class DirEntry:
    """A single entry in a picker-style directory listing."""

    path: Path
    is_dir: bool
    matches_filter: bool


SymlinkPolicy = Literal["reject", "preserve"]


def is_within(path: Path, anchor: Path) -> bool:
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


def scan_directory(
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

        if not is_within(entry, resolved_root):
            continue

        if entry.is_dir():
            children = scan_directory(root, entry, resolved_root)
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


def list_directory(
    base: Path,
    *,
    extensions: frozenset[str] | None = None,
    symlinks: SymlinkPolicy = "reject",
) -> list[DirEntry]:
    """List the immediate children of ``base`` for a picker UI.

    Does not recurse. Directories are always returned (so the user can
    navigate into them); files are annotated with ``matches_filter``
    according to ``extensions`` so the caller can visually distinguish
    them without hiding them outright.

    Args:
        base: The directory whose children to list.
        extensions: If set, files whose lowercased suffix is in this
            set have ``matches_filter=True``; other files have
            ``matches_filter=False``. If ``None``, all files match.
        symlinks: ``"reject"`` skips symlinks entirely (default, matches
            the file browser's security posture). ``"preserve"`` keeps
            them visible without resolving — required for picking a
            venv's ``python3.x`` symlink.

    Returns:
        Directories first (alphabetical), then files (alphabetical).
        Returns [] on PermissionError.
    """
    try:
        raw_entries = sorted(
            base.iterdir(),
            key=lambda p: (not p.is_dir(), p.name.lower()),
        )
    except PermissionError, OSError:
        return []

    result: list[DirEntry] = []
    for entry in raw_entries:
        if symlinks == "reject" and entry.is_symlink():
            continue

        if entry.is_dir():
            result.append(DirEntry(path=entry, is_dir=True, matches_filter=True))
        elif entry.is_file():
            matches = extensions is None or entry.suffix.lower() in extensions
            result.append(DirEntry(path=entry, is_dir=False, matches_filter=matches))

    return result
