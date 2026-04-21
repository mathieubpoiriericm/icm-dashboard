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
# Secondary cap for what actually reaches the browser's ui.code element.
# A 20 MB JSON reformatted with indent=2 can exceed 50 MB; sending that as
# one WebSocket message can freeze low-memory clients for seconds. Truncate
# at this cap and append a notice instead of the full content.
MAX_TEXT_DISPLAY_BYTES: int = 2 * 1024 * 1024  # 2 MB
# Hard cap on rows materialized into the browser table. A 50 MB CSV with
# narrow columns yields hundreds of thousands of rows; without this cap the
# pandas DataFrame plus the JSON serialization to the browser blow up memory.
MAX_CSV_ROWS: int = 1000

_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif"})
_TEXT_EXTS = frozenset({".md", ".txt", ".log"})

# 25% floor lets oversized plots shrink past "fit"; 400% ceiling covers
# pixel-level inspection of small plots.
_IMAGE_ZOOM_STEP: float = 0.25
_IMAGE_ZOOM_MIN: float = 0.25
_IMAGE_ZOOM_MAX: float = 4.0


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


def _exceeds_display_cap(content: str) -> bool:
    """True if `content` would exceed the byte cap when sent to the browser.

    Codepoint count ≥ byte cap ⇒ definitely over (1 codepoint ≥ 1 byte).
    Otherwise pay the encode to disambiguate the multi-byte (CJK, emoji) case.
    """
    if len(content) > MAX_TEXT_DISPLAY_BYTES:
        return True
    return len(content.encode("utf-8", errors="replace")) > MAX_TEXT_DISPLAY_BYTES


def _truncate_for_display(content: str) -> str:
    """Cap ui.code payload so a large file doesn't freeze the browser."""
    if not _exceeds_display_cap(content):
        return content
    truncated_bytes = content.encode("utf-8", errors="replace")[:MAX_TEXT_DISPLAY_BYTES]
    truncated = truncated_bytes.decode("utf-8", errors="ignore")
    return (
        truncated
        + "\n\n... [truncated: content exceeds "
        + f"{MAX_TEXT_DISPLAY_BYTES // (1024 * 1024)} MB display cap]"
    )


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
        # json.dumps(indent=2) can balloon compact JSON 3-5× in memory; skip
        # the round-trip when content is already over the display cap.
        if _exceeds_display_cap(content):
            formatted = content
        else:
            try:
                formatted = json.dumps(json.loads(content), indent=2)
            except json.JSONDecodeError:
                formatted = content
        with container:
            ui.code(_truncate_for_display(formatted), language="json")

    elif file_type == "csv":
        try:
            # Read one extra row so "exactly MAX_CSV_ROWS" can be distinguished
            # from "truncated" — nrows=MAX_CSV_ROWS alone gives len == cap in
            # both cases, causing a false "Showing first N rows" label on
            # files that happen to have exactly the cap.
            df = cast(
                pd.DataFrame,
                await asyncio.to_thread(
                    pd.read_csv,
                    file_path,
                    encoding="utf-8",
                    nrows=MAX_CSV_ROWS + 1,
                ),
            )
        except (OSError, UnicodeDecodeError, pd.errors.ParserError) as e:
            with container:
                ui.label(f"Error reading CSV: {e}").classes("text-negative")
            return
        truncated = len(df) > MAX_CSV_ROWS
        if truncated:
            df = df.iloc[:MAX_CSV_ROWS]
        with container:
            if truncated:
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
        _render_image_viewer(container, f"data:{mime};base64,{data}")

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
                f'<iframe src="data:application/pdf;base64,{data}" '
                f'class="file-preview-pdf"></iframe>'
            )

    elif file_type == "text":
        try:
            content = await asyncio.to_thread(file_path.read_text, encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            with container:
                ui.label(f"Error reading file: {e}").classes("text-negative")
            return
        with container:
            ui.code(_truncate_for_display(content))

    else:
        with container:
            ui.label("Unsupported file type.")


def _render_image_viewer(container: Element, src: str) -> None:
    """Render an image preview with fit-to-container default and zoom.

    At 100% (default) the image is scaled down if needed so the whole file
    is visible without cropping, preserving aspect ratio. Zooming past 100%
    grows the image beyond the viewport; the viewport's overflow-auto then
    provides horizontal and vertical scrollbars.
    """
    from nicegui import ui

    zoom = [1.0]
    pct_label: list[ui.label] = []
    viewport_ref: list[Element] = []
    zoom_out_btn: list[ui.button] = []
    zoom_in_btn: list[ui.button] = []
    # Cached last-emitted enabled states; set_enabled still sends a prop
    # patch on same-value writes, and every click toggles only one button's
    # state at the extremes — the other stays enabled and would re-emit.
    last_enabled = {"out": True, "in": True}

    def _apply() -> None:
        z = zoom[0]
        pct = int(round(z * 100))
        pct_label[0].set_text(f"{pct}%")
        vp = viewport_ref[0]
        if z == 1.0:
            vp.classes(remove="zoomed")
            vp.style(remove="--image-zoom")
        else:
            vp.classes(add="zoomed")
            vp.style(replace=f"--image-zoom: {z}")
        out_enabled = z > _IMAGE_ZOOM_MIN
        in_enabled = z < _IMAGE_ZOOM_MAX
        if out_enabled != last_enabled["out"]:
            zoom_out_btn[0].set_enabled(out_enabled)
            last_enabled["out"] = out_enabled
        if in_enabled != last_enabled["in"]:
            zoom_in_btn[0].set_enabled(in_enabled)
            last_enabled["in"] = in_enabled

    def _zoom_in() -> None:
        new_z = min(_IMAGE_ZOOM_MAX, round(zoom[0] + _IMAGE_ZOOM_STEP, 2))
        if new_z == zoom[0]:
            return
        zoom[0] = new_z
        _apply()

    def _zoom_out() -> None:
        new_z = max(_IMAGE_ZOOM_MIN, round(zoom[0] - _IMAGE_ZOOM_STEP, 2))
        if new_z == zoom[0]:
            return
        zoom[0] = new_z
        _apply()

    def _zoom_reset() -> None:
        if zoom[0] == 1.0:
            return
        zoom[0] = 1.0
        _apply()

    with container, ui.element("div").classes("image-viewer"):
        with ui.row().classes("image-viewer-controls items-center gap-xs no-wrap"):
            zoom_out_btn.append(
                ui.button(icon="zoom_out", on_click=_zoom_out)
                .props("flat round size=sm")
                .classes("btn-icon")
            )
            pct_label.append(ui.label("100%").classes("numeric image-viewer-pct"))
            zoom_in_btn.append(
                ui.button(icon="zoom_in", on_click=_zoom_in)
                .props("flat round size=sm")
                .classes("btn-icon")
            )
            ui.button(icon="fit_screen", on_click=_zoom_reset).props(
                "flat round size=sm"
            ).classes("btn-icon")
        viewport_ref.append(ui.element("div").classes("image-viewer-viewport"))
        with viewport_ref[0]:
            img_el = ui.element("img").classes("file-preview-image")
            img_el.props["src"] = src
            img_el.props["alt"] = ""
