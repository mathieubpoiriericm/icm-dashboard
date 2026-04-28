"""Small NiceGUI form primitives used by pipeline app pages."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from nicegui import ui


def bound_input(
    obj: object,
    attr: str,
    *,
    label: str,
    classes: str = "w-full",
    **kwargs: Any,
) -> ui.input:
    """Create an input bound to ``obj.attr``."""
    return (
        ui.input(label=label, value=getattr(obj, attr), **kwargs)
        .classes(classes)
        .bind_value(obj, attr)
    )


def bound_number(
    obj: object,
    attr: str,
    *,
    label: str,
    classes: str = "w-full",
    **kwargs: Any,
) -> ui.number:
    """Create a number input bound to ``obj.attr``."""
    return (
        ui.number(label=label, value=getattr(obj, attr), **kwargs)
        .classes(classes)
        .bind_value(obj, attr)
    )


def bound_select(
    obj: object,
    attr: str,
    *,
    options: Any,
    label: str,
    classes: str = "w-full",
    **kwargs: Any,
) -> ui.select:
    """Create a select bound to ``obj.attr``."""
    return (
        ui.select(options=options, label=label, value=getattr(obj, attr), **kwargs)
        .classes(classes)
        .bind_value(obj, attr)
    )


def bound_path_input(
    obj: object,
    attr: str,
    *,
    label: str,
    on_pick: Callable[[ui.input], Awaitable[None]],
) -> ui.input:
    """Create a bound path input with the standard folder picker button."""
    with ui.row().classes("w-full items-center gap-xs no-wrap"):
        inp = bound_input(obj, attr, label=label, classes="flex-1")
        ui.button(
            icon="folder_open",
            on_click=lambda: on_pick(inp),
        ).props("flat dense").classes("btn-icon")
    return inp
