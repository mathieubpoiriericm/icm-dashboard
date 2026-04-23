"""Filesystem navigation helpers shared by file browser and path picker."""

from __future__ import annotations

import os
import os.path
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

#: Hard cap on scan_directory recursion depth. CPython's default
#: ``sys.getrecursionlimit()`` is ~1000, and a pathological bind-mount loop
#: that re-includes an ancestor would crash with RecursionError (which the
#: OSError handler doesn't catch, so it would escape to the caller and
#: freeze the tree pane). 20 levels covers any legitimate project tree.
_MAX_SCAN_DEPTH: int = 20


def is_within(path: Path, anchor: Path) -> bool:
    """True iff ``path`` resolves inside ``anchor``.

    ``anchor`` is expected to already be resolved. ``OSError`` covers
    the case where ``path.resolve()`` fails on a missing or unreadable
    intermediate component.
    """
    try:
        path.resolve().relative_to(anchor)
    except (OSError, ValueError):
        return False
    return True


def scan_directory(
    root: Path,
    base: Path,
    resolved_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Recursively scan a directory and return ui.tree-compatible nodes.

    Uses ``os.scandir`` so each entry's type checks read from the cached
    dirent instead of issuing extra stat syscalls. After the symlink filter,
    every non-symlink entry is structurally contained within ``resolved_root``
    (recursion never follows a symlink out of the tree) so per-entry
    ``is_within`` is unnecessary here.

    Args:
        root: The project root (trust anchor for path validation).
        base: The directory to scan.
        resolved_root: Pre-resolved root path (avoids repeated resolve calls).

    Returns:
        List of tree node dicts with id, label, children keys.
    """
    if resolved_root is None:
        resolved_root = root.resolve()
    return _scan_directory(root, base, resolved_root, 0)


def _scan_directory(
    root: Path,
    base: Path,
    resolved_root: Path,
    depth: int,
) -> list[dict[str, Any]]:
    # Depth cap guards against bind-mount loops that would overflow CPython's
    # recursion limit before OSError can catch anything.
    if depth >= _MAX_SCAN_DEPTH:
        return []
    try:
        with os.scandir(base) as it:
            raw_entries = list(it)
    except (PermissionError, FileNotFoundError, NotADirectoryError, OSError):
        return []

    raw_entries.sort(
        key=lambda e: (not e.is_dir(follow_symlinks=False), e.name),
    )

    nodes: list[dict[str, Any]] = []
    for entry in raw_entries:
        if entry.is_symlink():
            continue

        if entry.is_dir(follow_symlinks=False):
            children = _scan_directory(
                root, Path(entry.path), resolved_root, depth + 1
            )
            if children:  # Exclude empty directories
                nodes.append(
                    {
                        "id": entry.path,
                        "label": entry.name,
                        "children": children,
                    }
                )
        elif entry.is_file(follow_symlinks=False):
            # splitext avoids constructing a Path just to read .suffix — the
            # scan can iterate thousands of entries, so the allocation matters.
            if os.path.splitext(entry.name)[1].lower() in SUPPORTED_EXTENSIONS:
                nodes.append(
                    {
                        "id": entry.path,
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
    def _is_dir_no_follow(p: Path) -> bool:
        # Path.is_dir() follows symlinks and will raise OSError on a broken
        # target or a stalled network mount; follow_symlinks=False uses the
        # dirent type without a stat() syscall, so a dead symlink can't
        # escape the PermissionError/OSError wrapper below.
        try:
            return p.is_dir(follow_symlinks=False)
        except OSError:
            return False

    try:
        raw_entries = sorted(
            base.iterdir(),
            key=lambda p: (not _is_dir_no_follow(p), p.name.lower()),
        )
    except (PermissionError, OSError):
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
