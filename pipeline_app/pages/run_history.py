"""Run History page — table of past pipeline runs with status badges."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nicegui import ui

from pipeline_app.components.async_loader import (
    load_io_bound_into,
    refresh_with_button,
)
from pipeline_app.components.confirm_dialog import confirm
from pipeline_app.components.empty_state import empty_state
from pipeline_app.components.table_utils import table_columns
from pipeline_app.config import clear_history, load_history
from pipeline_app.pages.results_viewer import is_safe_report_id


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

    columns = table_columns(
        [
            ("started_at", "Started At"),
            ("run_mode", "Mode"),
            ("status", "Status"),
            ("exit_code", "Exit Code"),
            ("actions", "Actions", False),
        ]
    )

    table_container: list[ui.element] = []
    refresh_btn_ref: list[ui.button] = []

    def _report_id_from_row(row: dict[str, Any]) -> str:
        report_path = row.get("report_path", "")
        if report_path:
            return Path(report_path).stem
        return row.get("id") or ""

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
                        class="btn-secondary"
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
                report_id = _report_id_from_row(row)
                if not report_id:
                    # Legacy record with neither report_path nor id — the
                    # results page would land on "Report not found: unknown"
                    # with only a Back button. Say so inline instead.
                    ui.notify("No report available for this run", color="warning")
                    return
                # Row payload comes from the browser and could be tampered with
                # before the $emit. Validate before navigating so a malformed
                # id can't be spliced into a multi-segment URL.
                if not is_safe_report_id(report_id):
                    ui.notify("Invalid report id", color="warning")
                    return
                ui.navigate.to(f"/results/{report_id}")

            table.on("view", _on_view)

    async def _load_and_render() -> None:
        """Load history off-loop, then replace the placeholder with the table."""
        if not table_container:
            return
        await load_io_bound_into(table_container[0], _get_rows, _build_table)

    async def _refresh_table() -> None:
        """Clear and rebuild the table with a brief loading indicator."""
        await refresh_with_button(refresh_btn_ref, _load_and_render)

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
        # Placeholder shown until the async loader swaps in the table; the
        # initial paint returns immediately without blocking on JSON I/O.
        ui.spinner("dots").classes("q-pa-md")

    ui.timer(0.0, _load_and_render, once=True)

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
        ).props("unelevated").classes("btn-destructive")
