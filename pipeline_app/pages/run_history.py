"""Run History page — table of past pipeline runs with status badges."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nicegui import run, ui

from pipeline_app.components.button_loading import button_loading
from pipeline_app.components.confirm_dialog import confirm
from pipeline_app.components.empty_state import empty_state
from pipeline_app.config import clear_history, load_history


def create_run_history_page() -> None:
    """Render the Run History page."""
    ui.label("Run History").classes("page-title")

    def _get_rows() -> list[dict[str, Any]]:
        history = load_history()
        rows = []
        for i, record in enumerate(history):
            status = record.get("status", "unknown")
            rid = record.get("id", "")
            rows.append(
                {
                    # Synthetic unique row key so rows with empty / missing
                    # id (legacy history records) don't collide under
                    # row_key="row_id" and silently drop from the table.
                    "row_id": f"{rid}_{i}",
                    "id": rid,
                    "started_at": record.get("started_at", ""),
                    "run_mode": record.get("run_mode", ""),
                    "status": status,
                    "exit_code": record.get("exit_code", ""),
                    "report_path": record.get("report_path", ""),
                }
            )
        return rows

    columns = [
        {
            "name": "started_at",
            "label": "Started At",
            "field": "started_at",
            "sortable": True,
        },
        {
            "name": "run_mode",
            "label": "Mode",
            "field": "run_mode",
            "sortable": True,
        },
        {
            "name": "status",
            "label": "Status",
            "field": "status",
            "sortable": True,
        },
        {
            "name": "exit_code",
            "label": "Exit Code",
            "field": "exit_code",
            "sortable": True,
        },
        {
            "name": "actions",
            "label": "Actions",
            "field": "actions",
        },
    ]

    table_container: list[ui.element] = []
    refresh_btn_ref: list[ui.button] = []

    def _report_id_from_row(row: dict[str, Any]) -> str:
        report_path = row.get("report_path", "")
        if report_path:
            return Path(report_path).stem
        return row.get("id", "unknown")

    def _build_table(rows: list[dict[str, Any]]) -> None:
        """Build the history table inside the current container."""
        if not rows:
            empty_state(
                "history",
                "No runs yet",
                "Pipeline runs appear here once you launch one from Configure & Run.",
                action_label="Go to Configure & Run",
                on_action=lambda: ui.navigate.to("/"),
            )
            return
        with ui.table(
            columns=columns,
            rows=rows,
            row_key="row_id",
        ).classes("w-full") as table:
            table.add_slot(
                "body-cell-status",
                # fmt: off
                """
                <q-td :props="props">
                    <span
                        :class="props.value === 'success' ? 'badge-success'
                              : props.value === 'failed' ? 'badge-error'
                              : 'badge-warning'"
                    >
                        {{ props.value }}
                    </span>
                </q-td>
                """,
                # fmt: on
            )
            table.add_slot(
                "body-cell-actions",
                """
                <q-td :props="props">
                    <q-btn
                        outline size="sm" icon="visibility" label="View"
                        @click="$parent.$emit('view', props.row)"
                    />
                </q-td>
                """,
            )

            def _on_view(e) -> None:
                # NiceGUI may deliver row payload as dict, list-wrapped dict,
                # or other shapes depending on Quasar version. Guard each case.
                args = getattr(e, "args", None)
                if isinstance(args, dict):
                    row = args
                elif isinstance(args, list) and args and isinstance(args[0], dict):
                    row = args[0]
                else:
                    ui.notify("Could not read row data", color="warning")
                    return
                ui.navigate.to(f"/results/{_report_id_from_row(row)}")

            table.on("view", _on_view)

    async def _refresh_table() -> None:
        """Clear and rebuild the table with a brief loading indicator."""
        if not refresh_btn_ref:
            return
        async with button_loading(refresh_btn_ref[0]):
            rows = await run.io_bound(_get_rows)
            if table_container:
                table_container[0].clear()
                with table_container[0]:
                    _build_table(rows)

    async def _clear_all() -> None:
        confirmed = await confirm(
            "Are you sure you want to clear all run history?",
            title="Clear History",
        )
        if not confirmed:
            return
        clear_history()
        ui.notify("History cleared", color="positive")
        await _refresh_table()

    with ui.column().classes("w-full") as cont:
        table_container.append(cont)
        _build_table(_get_rows())

    with ui.row().classes("q-mt-md gap-sm"):
        refresh_btn = (
            ui.button(
                "Refresh",
                on_click=_refresh_table,
                icon="refresh",
            )
            .props("outline")
            .classes("btn-secondary")
        )
        refresh_btn_ref.append(refresh_btn)
        ui.button(
            "Clear All",
            on_click=_clear_all,
            icon="delete_forever",
            color="negative",
        ).props("flat").classes("theme-btn-ghost")
