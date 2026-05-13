"""Preset selector component for the HPC Configure & Run page.

Mirrors `pipeline_app/components/preset_selector.py` but persists into the
HPC app's own preset store (`pipeline_app_hpc/presets.json`) and operates
on `HpcAppConfig`, whose fields don't match the prod `PipelineAppConfig`.
Sharing the prod component would silently route HPC presets into the prod
store and round-trip them through the wrong dataclass.
"""

from __future__ import annotations

import dataclasses

from nicegui import ui
from pipeline_app.components.confirm_dialog import confirm
from pipeline_app.components.preset_dialog import prompt_preset_name

from pipeline_app_hpc.config import (
    HpcAppConfig,
    delete_preset,
    load_preset,
    load_presets,
    upsert_preset,
)


def create_preset_selector(config: HpcAppConfig) -> None:
    """Render and wire the preset select/load/save/delete controls."""
    ui.label("Presets").classes("section-header")
    presets = load_presets()
    preset_select = ui.select(
        options={p.id: p.name for p in presets},
        label="Preset",
        value=None,
    ).classes("w-full")

    def _replace_options(updated) -> None:
        preset_select.options = {p.id: p.name for p in updated}
        preset_select.update()

    async def _load_preset(preset_id: str | None) -> None:
        if not preset_id:
            ui.notify("No preset selected", color="warning")
            return
        confirmed = await confirm(
            "Loading this preset will overwrite the current form settings. Continue?",
            title="Load Preset",
        )
        if not confirmed:
            return
        loaded = load_preset(preset_id)
        if loaded is None:
            ui.notify("Preset not found", color="negative")
            return
        for field in dataclasses.fields(loaded):
            setattr(config, field.name, getattr(loaded, field.name))
        ui.notify("Preset loaded", color="positive")

    async def _save_current_preset() -> None:
        name = await prompt_preset_name()
        if not name:
            return
        existing_names = {p.name for p in load_presets()}
        if name in existing_names:
            confirmed = await confirm(
                f"A preset named '{name}' already exists. Overwrite it?",
                title="Overwrite Preset",
            )
            if not confirmed:
                return
        updated = upsert_preset(name, config)
        ui.notify(f"Saved preset: {name}", color="positive")
        _replace_options(updated)
        preset_select.value = next((p.id for p in updated if p.name == name), None)
        preset_select.update()

    async def _delete_preset(preset_id: str | None) -> None:
        if not preset_id:
            ui.notify("No preset selected", color="warning")
            return
        confirmed = await confirm(
            "Are you sure you want to delete this preset?",
            title="Delete Preset",
        )
        if not confirmed:
            return
        updated = delete_preset(preset_id)
        ui.notify("Preset deleted", color="positive")
        _replace_options(updated)
        preset_select.value = None
        preset_select.update()

    with ui.row().classes("q-mb-md gap-sm"):
        ui.button(
            "Load",
            on_click=lambda: _load_preset(preset_select.value),
            icon="download",
        ).props("flat").classes("btn-ghost")
        ui.button(
            "Save",
            on_click=_save_current_preset,
            icon="save",
        ).props("flat").classes("btn-ghost")
        ui.button(
            "Delete",
            on_click=lambda: _delete_preset(preset_select.value),
            icon="delete",
        ).props("unelevated").classes("btn-destructive")
