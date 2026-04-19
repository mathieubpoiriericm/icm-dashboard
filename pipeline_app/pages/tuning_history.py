"""Tuning History page — metrics table with comparison view."""

from __future__ import annotations

import csv
import logging

from nicegui import ui

from pipeline_app.components.empty_state import empty_state
from pipeline_app.runner import resolve_project_root

logger = logging.getLogger(__name__)

# Mirror of the numeric subset of CSV_COLUMNS in
# scripts/tuning/track_run.py. Kept manually in sync because track_run.py is
# a standalone CLI script, not importable from pipeline_app/.
NUMERIC_COLUMNS: frozenset[str] = frozenset(
    {
        "precision",
        "recall",
        "f1",
        "f2",
        "threshold",
        "tp",
        "fp",
        "fn",
        "tn",
        "true_positives",
        "false_positives",
        "fn_threshold",
        "fn_miss",
        "composite_score",
        "confidence_threshold",
        "f_beta_weight",
        "total_extracted",
        "total_validated",
        "total_rejected",
        "total_genes",
        "total_papers",
        "acceptance_rate",
        "estimated_cost_usd",
        "input_tokens",
        "output_tokens",
        "thinking_tokens",
        "total_processing_time",
        "llm_time",
    }
)


def _load_tuning_runs(project_root: str) -> list[dict[str, str]]:
    """Load tuning runs from CSV file, newest first."""
    root = resolve_project_root(project_root)
    csv_path = root / "logs" / "tuning" / "tuning_runs.csv"
    if not csv_path.exists():
        return []
    try:
        with csv_path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
    except (OSError, UnicodeDecodeError, csv.Error) as e:
        logger.warning("Failed to read tuning_runs.csv: %s", e)
        return []
    # Newest first — assume rows are in append order
    return list(reversed(rows))


def _compute_display_keys(
    all_keys: list[str],
    preferred_keys: list[str],
) -> list[str]:
    """Order columns: preferred first, then leftovers, hiding ``_row_id``.

    The synthetic ``_row_id`` key is injected only as the table's row_key —
    it must not surface as a visible column.
    """
    display_keys = [k for k in preferred_keys if k in all_keys]
    display_keys += [k for k in all_keys if k not in display_keys and k != "_row_id"]
    return display_keys


def _diff_value(v1: str, v2: str, col: str) -> str:
    """Compute the numeric diff between two values."""
    if col not in NUMERIC_COLUMNS:
        return ""
    try:
        diff = float(v2) - float(v1)
        sign = "+" if diff > 0 else ""
        return f"{sign}{diff:.4f}"
    except ValueError, TypeError:
        return ""


def create_tuning_history_page(project_root: str) -> None:
    """Render the Tuning History page."""
    ui.label("Tuning History").classes("page-title")

    rows = _load_tuning_runs(project_root)
    for i, row in enumerate(rows):
        row["_row_id"] = str(i)

    if not rows:
        empty_state(
            "analytics",
            "No tuning runs yet",
            "Tuning runs are recorded in logs/tuning/tuning_runs.csv. "
            "Launch one from the Tuning tab to get started.",
            action_label="Go to Tuning",
            on_action=lambda: ui.navigate.to("/tuning"),
        )
        return

    all_keys = list(rows[0].keys()) if rows else []
    preferred_keys = [
        "timestamp",
        "run_group",
        "prompt_version",
        "llm_model",
        "confidence_threshold",
        "f_beta_weight",
        "precision",
        "recall",
        "f1",
        "f2",
        "true_positives",
        "false_positives",
        "fn_threshold",
        "fn_miss",
        "notes",
    ]
    display_keys = _compute_display_keys(all_keys, preferred_keys)

    columns = [
        {
            "name": k,
            "label": k.replace("_", " ").title(),
            "field": k,
            "sortable": True,
        }
        for k in display_keys
    ]

    selected_rows: list[dict[str, str]] = []
    comparison_container: list[ui.element] = []

    def _on_selection(e) -> None:
        selected_rows.clear()
        selected_rows.extend(e.selection)
        _update_comparison()

    def _update_comparison() -> None:
        if comparison_container:
            comparison_container[0].clear()
        if len(selected_rows) != 2:
            return
        row1, row2 = selected_rows[0], selected_rows[1]
        with comparison_container[0]:
            ui.label("Comparison (Row 1 vs Row 2)").classes(
                "section-header q-mb-md q-mt-md"
            )
            comp_cols = [
                {"name": "metric", "label": "Metric", "field": "metric"},
                {"name": "row1", "label": "Run 1", "field": "row1"},
                {"name": "row2", "label": "Run 2", "field": "row2"},
                {"name": "diff", "label": "Diff (2 - 1)", "field": "diff"},
            ]
            comp_rows = []
            for k in display_keys:
                v1 = row1.get(k, "")
                v2 = row2.get(k, "")
                diff = _diff_value(v1, v2, k)
                comp_rows.append(
                    {
                        "metric": k.replace("_", " ").title(),
                        "row1": v1,
                        "row2": v2,
                        "diff": diff,
                    }
                )
            comp_table = ui.table(
                columns=comp_cols,
                rows=comp_rows,
                row_key="metric",
            ).classes("w-full")
            comp_table.add_slot(
                "body-cell-diff",
                """
                <q-td :props="props">
                    <span
                        :class="props.value.startsWith('+') ? 'diff-positive'
                              : props.value.startsWith('-') ? 'diff-negative'
                              : ''"
                    >
                        {{ props.value }}
                    </span>
                </q-td>
                """,
            )

    table = ui.table(
        columns=columns,
        rows=rows,
        row_key="_row_id",
        selection="multiple",
    ).classes("w-full")
    table.on_select(_on_selection)

    with ui.row().classes("q-mt-sm items-center gap-sm"):
        ui.label("Select exactly 2 rows to compare metrics.").classes("text-muted")
        ui.button(
            "Refresh",
            on_click=lambda: ui.navigate.reload(),
            icon="refresh",
        ).props("outline size=sm")

    with ui.column().classes("w-full") as comp_cont:
        comparison_container.append(comp_cont)
