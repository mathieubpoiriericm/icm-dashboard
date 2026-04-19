"""Async context manager for transient button loading states."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from nicegui import ui


@asynccontextmanager
async def button_loading(btn: ui.button) -> AsyncIterator[None]:
    """Show a spinner on the button while the wrapped block runs.

    Wrap the caller's side-effects in ``suppress(RuntimeError)`` so that a
    client navigating away mid-operation doesn't raise when we try to
    clear the loading prop on the now-detached element.
    """
    btn.props("loading")
    try:
        yield
    finally:
        with suppress(RuntimeError):
            btn.props(remove="loading")
