"""File type detection and content viewer rendering."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pandas as pd

if TYPE_CHECKING:
    from nicegui.element import Element

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".json",
        ".csv",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".pdf",
        ".md",
        ".txt",
        ".log",
    }
)

MAX_FILE_SIZE: int = 50 * 1024 * 1024  # 50 MB
MAX_BINARY_FILE_SIZE: int = 10 * 1024 * 1024  # 10 MB (images/PDFs embed as data URIs)
# Hard cap on rows materialized into the browser table. A 50 MB CSV with
# narrow columns yields hundreds of thousands of rows; without this cap the
# pandas DataFrame plus the JSON serialization to the browser blow up memory.
MAX_CSV_ROWS: int = 1000

_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif"})
_TEXT_EXTS = frozenset({".md", ".txt", ".log"})


def detect_file_type(filename: str) -> str | None:
    """Classify a filename into json/csv/image/pdf/text or None."""
    ext = Path(filename).suffix.lower()
    if ext == ".json":
        return "json"
    if ext == ".csv":
        return "csv"
    if ext in _IMAGE_EXTS:
        return "image"
    if ext == ".pdf":
        return "pdf"
    if ext in _TEXT_EXTS:
        return "text"
    return None


async def render_file_content(
    file_path: Path,
    container: Element,
) -> None:
    """Render file content inside the given NiceGUI container.

    All file I/O runs in a worker thread so a large or slow read doesn't
    block the asyncio event loop and stall every other connected client.
    """
    from nicegui import ui

    container.clear()

    # Single try/except around stat() so a TOCTOU between exists() and stat()
    # (file rotated or deleted by the pipeline mid-render) surfaces as a
    # readable label instead of an uncaught FileNotFoundError.
    try:
        size = await asyncio.to_thread(lambda: file_path.stat().st_size)
    except (FileNotFoundError, OSError) as e:
        with container:
            ui.label(f"File no longer accessible: {e}").classes("text-negative")
        return

    file_type = detect_file_type(file_path.name)
    is_binary = file_type in ("image", "pdf")
    size_limit = MAX_BINARY_FILE_SIZE if is_binary else MAX_FILE_SIZE
    if size > size_limit:
        limit_mb = size_limit / 1024 / 1024
        type_label = file_type or "this"
        with container:
            ui.label(
                f"File too large ({size / 1024 / 1024:.1f} MB). "
                f"Limit is {limit_mb:.0f} MB for {type_label} files."
            ).classes("text-negative")
        return

    if file_type == "json":
        try:
            content = await asyncio.to_thread(file_path.read_text, encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            with container:
                ui.label(f"Error reading file: {e}").classes("text-negative")
            return
        try:
            formatted = json.dumps(json.loads(content), indent=2)
        except json.JSONDecodeError:
            formatted = content
        with container:
            ui.code(formatted, language="json")

    elif file_type == "csv":
        try:
            # nrows= avoids iterator/chunksize, so read_csv always returns a
            # DataFrame — cast narrows the static union without a branch.
            df = cast(
                pd.DataFrame,
                await asyncio.to_thread(
                    pd.read_csv,
                    file_path,
                    encoding="utf-8",
                    nrows=MAX_CSV_ROWS,
                ),
            )
        except (OSError, UnicodeDecodeError, pd.errors.ParserError) as e:
            with container:
                ui.label(f"Error reading CSV: {e}").classes("text-negative")
            return
        with container:
            if len(df) >= MAX_CSV_ROWS:
                ui.label(f"Showing first {MAX_CSV_ROWS} rows only.").classes(
                    "text-muted"
                )
            ui.table.from_pandas(df).classes("w-full")

    elif file_type == "image":
        try:
            raw = await asyncio.to_thread(file_path.read_bytes)
        except OSError as e:
            with container:
                ui.label(f"Error reading file: {e}").classes("text-negative")
            return
        data = base64.b64encode(raw).decode()
        ext = file_path.suffix.lower().lstrip(".")
        mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
        with container:
            ui.image(f"data:{mime};base64,{data}")

    elif file_type == "pdf":
        try:
            raw = await asyncio.to_thread(file_path.read_bytes)
        except OSError as e:
            with container:
                ui.label(f"Error reading file: {e}").classes("text-negative")
            return
        data = base64.b64encode(raw).decode()
        with container:
            ui.html(
                f'<iframe src="data:application/pdf;base64,'
                f'{data}" width="100%" height="800px"></iframe>'
            )

    elif file_type == "text":
        try:
            content = await asyncio.to_thread(file_path.read_text, encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            with container:
                ui.label(f"Error reading file: {e}").classes("text-negative")
            return
        with container:
            ui.code(content)

    else:
        with container:
            ui.label("Unsupported file type.")
