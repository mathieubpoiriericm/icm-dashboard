"""Reusable async confirmation dialog component."""

from __future__ import annotations

from contextlib import suppress

from nicegui import ui


async def confirm(message: str, title: str = "Confirm") -> bool:
    """Show a confirmation dialog and return True if confirmed.

    Args:
        message: The confirmation prompt text.
        title: Dialog title.

    Returns:
        True if user clicked Confirm, False if Cancel or closed.
    """
    with ui.dialog() as dialog, ui.card().classes("confirm-dialog theme-card q-pa-md"):
        ui.label(title).classes("section-header")
        ui.label(message).classes("text-muted q-my-sm")
        with ui.row().classes("w-full justify-end gap-sm q-mt-md"):
            ui.button(
                "Cancel",
                on_click=lambda: dialog.submit(False),
            ).props("flat size=sm").classes("btn-ghost")
            ui.button(
                "Confirm",
                on_click=lambda: dialog.submit(True),
            ).props("flat size=sm").classes("btn-destructive")
    result = await dialog
    with suppress(RuntimeError):
        dialog.delete()
    return bool(result)
