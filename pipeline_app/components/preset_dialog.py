"""Preset name input dialog component."""

from __future__ import annotations

from contextlib import suppress

from nicegui import ui


async def prompt_preset_name() -> str | None:
    """Show a dialog prompting for a preset name.

    Returns:
        The entered name, or None if cancelled.
    """
    with ui.dialog() as dialog, ui.card().classes("confirm-dialog theme-card q-pa-md"):
        ui.label("Save Preset").classes("section-header")
        name_input = ui.input(label="Preset Name").classes("w-full q-my-sm")

        def _on_save() -> None:
            name = (name_input.value or "").strip()
            if not name:
                ui.notify("Preset name required", color="warning")
                return
            dialog.submit(name)

        with ui.row().classes("w-full justify-end gap-sm q-mt-md"):
            ui.button(
                "Cancel",
                on_click=lambda: dialog.submit(None),
            ).props("flat size=sm").classes("btn-ghost")
            ui.button(
                "Save",
                on_click=_on_save,
            ).props("unelevated size=sm").classes("btn-primary")
    result = await dialog
    with suppress(RuntimeError):
        dialog.delete()
    return result or None
