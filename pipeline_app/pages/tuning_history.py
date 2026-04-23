"""Tuning History page — metrics table with comparison view."""

from __future__ import annotations

import contextlib
import csv
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from nicegui import run, ui

from pipeline_app.components.button_loading import button_loading
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

# Metrics where a *decrease* is an improvement (lower cost, fewer errors,
# shorter runtime). The diff-coloring template inverts the sign→color
# mapping for these so that green always means "better".
LOWER_IS_BETTER: frozenset[str] = frozenset(
    {
        "fp",
        "fn",
        "false_positives",
        "fn_threshold",
        "fn_miss",
        "total_rejected",
        "estimated_cost_usd",
        "input_tokens",
        "output_tokens",
        "thinking_tokens",
        "total_processing_time",
        "llm_time",
    }
)


def _load_tuning_runs(project_root: str) -> list[dict[str, object]]:
    """Load tuning runs from CSV file, newest first.

    Coerce NUMERIC_COLUMNS values to float so the Quasar table sorts them
    numerically — otherwise '15' < '5' as strings and integer count
    columns render in the wrong order.
    """
    root = resolve_project_root(project_root)
    csv_path = root / "logs" / "tuning" / "tuning_runs.csv"
    if not csv_path.exists():
        return []
    try:
        with csv_path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            raw_rows = list(reader)
    except (OSError, UnicodeDecodeError, csv.Error) as e:
        logger.warning("Failed to read tuning_runs.csv: %s", e)
        return []
    coerced: list[dict[str, object]] = []
    for r in raw_rows:
        row: dict[str, object] = dict(r)
        for k in NUMERIC_COLUMNS:
            v = row.get(k)
            if isinstance(v, str) and v:
                # Leave as string on parse failure so the cell still renders;
                # this one malformed row loses numeric sort.
                with contextlib.suppress(ValueError):
                    row[k] = float(v)
        coerced.append(row)
    # Sort by timestamp when available so out-of-order writes still render
    # newest-first; fall back to file order when the column is missing.
    if coerced and any(r.get("timestamp") for r in coerced):
        return sorted(coerced, key=_timestamp_sort_key, reverse=True)
    return list(reversed(coerced))


def _timestamp_sort_key(row: dict[str, object]) -> tuple[int, float, str]:
    """Parse an ISO timestamp for ordering; fall back to the raw string.

    Lexicographic string sort is only correct when the timestamp format is
    uniform; parsed datetimes handle microsecond presence and UTC-suffix
    vs offset variations correctly. The leading int keeps unparseable
    rows (tier 0) together and below parsed rows (tier 1) so cross-tier
    comparisons never touch the heterogeneous float/string tail.
    """
    raw = row.get("timestamp")
    if not isinstance(raw, str) or not raw:
        return (0, 0.0, "")
    try:
        # `datetime.fromisoformat` accepts naive and offset-qualified ISO;
        # normalize a trailing "Z" to "+00:00" for older Python versions
        # and then coerce any naive result to UTC so .timestamp() is
        # deterministic across mixed-TZ rows.
        iso = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return (1, dt.timestamp(), raw)
    except ValueError:
        return (0, 0.0, raw)


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


def _diff_value(v1: object, v2: object, col: str) -> str:
    """Compute the numeric diff between two values.

    Accepts str, int, or float; anything else yields "".
    """
    if col not in NUMERIC_COLUMNS:
        return ""
    if not isinstance(v1, (str, int, float)) or not isinstance(v2, (str, int, float)):
        return ""
    try:
        diff = float(v2) - float(v1)
        sign = "+" if diff > 0 else ""
        return f"{sign}{diff:.4f}"
    except (ValueError, TypeError):
        return ""


def _diff_direction(col: str) -> str:
    """Return "lower" if a decrease is an improvement, else "higher"."""
    return "lower" if col in LOWER_IS_BETTER else "higher"


def create_tuning_history_page(project_root: str) -> None:
    """Render the Tuning History page."""
    ui.label("Tuning History").classes("page-title")

    container = ui.column().classes("w-full")
    with container:
        ui.spinner("dots").classes("q-pa-md")

    refresh_btn_ref: list[ui.button] = []

    async def _load() -> None:
        rows = await run.io_bound(_load_tuning_runs, project_root)
        for i, row in enumerate(rows):
            row["_row_id"] = str(i)
        # Disconnect mid-io_bound leaves the container attached to a
        # disposed client — NiceGUI raises RuntimeError on clear/mount.
        with contextlib.suppress(RuntimeError):
            container.clear()
            with container:
                _render_body(rows, _refresh, refresh_btn_ref)

    async def _refresh() -> None:
        # button_loading both disables the button and serves as the in-flight
        # guard: rapid re-clicks during the io_bound CSV read would otherwise
        # race multiple container.clear() + rebuild passes.
        if not refresh_btn_ref:
            await _load()
            return
        async with button_loading(refresh_btn_ref[0]):
            await _load()

    ui.timer(0.0, _load, once=True)


def _render_body(
    rows: list[dict[str, object]],
    on_refresh: Callable[[], Awaitable[None]] | None = None,
    refresh_btn_ref: list[ui.button] | None = None,
) -> None:
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

    selected_rows: list[dict[str, object]] = []

    @ui.refreshable
    def _comparison_panel() -> None:
        if len(selected_rows) != 2:
            return
        row1, row2 = selected_rows[0], selected_rows[1]
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
                    # "lower" → invert sign→color mapping so a *decrease* in
                    # cost / latency / false positives shows green.
                    "direction": _diff_direction(k),
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
                    :class="(() => {
                        const v = props.value;
                        if (!v.startsWith('+') && !v.startsWith('-')) return '';
                        const isPlus = v.startsWith('+');
                        const lower = props.row.direction === 'lower';
                        const good = lower ? !isPlus : isPlus;
                        return good ? 'diff-positive' : 'diff-negative';
                    })()"
                >
                    {{ props.value }}
                </span>
            </q-td>
            """,
        )

    def _on_selection(e) -> None:
        # Older NiceGUI/Quasar combinations occasionally deliver an event
        # without `.selection`; fall back to an empty list so the handler
        # doesn't crash and leave the diff panel silently out of sync.
        selected_rows.clear()
        selected_rows.extend(getattr(e, "selection", []))
        # A deferred select event from a pre-refresh table targets an
        # orphaned _comparison_panel; .refresh() raises RuntimeError.
        with contextlib.suppress(RuntimeError):
            _comparison_panel.refresh()

    table = ui.table(
        columns=columns,
        rows=rows,
        row_key="_row_id",
        selection="multiple",
    ).classes("w-full")
    table.on_select(_on_selection)

    with ui.row().classes("q-mt-sm items-center gap-sm"):
        ui.label("Select exactly 2 rows to compare metrics.").classes("text-muted")
        refresh_btn = (
            ui.button(
                "Refresh",
                on_click=on_refresh if on_refresh is not None else ui.navigate.reload,
                icon="refresh",
            )
            .props("outline")
            .classes("btn-secondary")
        )
        if refresh_btn_ref is not None:
            refresh_btn_ref.clear()
            refresh_btn_ref.append(refresh_btn)

    _comparison_panel()
