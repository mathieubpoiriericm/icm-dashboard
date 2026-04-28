"""Helpers for async page sections that load data off the event loop."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import suppress

from nicegui import run, ui


async def load_io_bound_into[T](
    container: ui.element,
    loader: Callable[..., T],
    renderer: Callable[[T], None],
    *args,
) -> None:
    """Run ``loader(*args)`` in a worker, then replace ``container`` contents."""
    result = await run.io_bound(loader, *args)
    with suppress(RuntimeError):
        container.clear()
        with container:
            renderer(result)


async def refresh_with_button(
    button_ref: list[ui.button],
    refresh: Callable[[], Awaitable[None]],
) -> None:
    """Run a refresh under the shared loading-button treatment."""
    if not button_ref:
        await refresh()
        return
    from pipeline_app.components.button_loading import button_loading

    async with button_loading(button_ref[0]):
        await refresh()
