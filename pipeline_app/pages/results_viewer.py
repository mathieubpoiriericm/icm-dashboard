"""Results Viewer page — tabbed report analysis."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from nicegui import ui

from pipeline_app.components.empty_state import empty_state
from pipeline_app.components.stat_card import stat_card
from pipeline_app.theme import (
    CHART_ACCENT_COLORS,
    COLORS,
    chart_axis_label,
    chart_split_line,
    chart_title,
)

_SAFE_REPORT_ID = re.compile(r"^[\w\-.:]+$")
_MAX_REPORT_SIZE: int = 50 * 1024 * 1024  # 50 MB cap on JSON report load


def _is_safe_report_id(report_id: str) -> bool:
    """Validate report_id matches the safe-chars regex AND is not a traversal.

    The base regex ``[\\w\\-.:]+`` accidentally matches ``..`` (two dots are
    word-set members for the regex but a path-traversal token for the
    filesystem). Reject any id whose dotted form could escape ``logs/json/``.
    """
    if not report_id or not _SAFE_REPORT_ID.match(report_id):
        return False
    return ".." not in report_id and not report_id.startswith(".")


def _gene_symbol(g: dict[str, Any]) -> str:
    """Extract gene symbol with key fallback."""
    return g.get("gene_symbol", g.get("symbol", ""))


def _gene_confidence(g: dict[str, Any]) -> float:
    """Extract and round confidence score with key fallback."""
    return round(g.get("confidence_score", g.get("confidence", 0)), 3)


def _join_field(g: dict[str, Any], *keys: str) -> str:
    """Pick the first non-empty value from ``keys`` and stringify it.

    GeneEntry list fields (gwas_trait, omics_evidence) need joining for
    display. The multi-key form supports renames across report versions.
    """
    val: Any = ""
    for k in keys:
        val = g.get(k, "")
        if val:
            break
    if isinstance(val, list):
        return ", ".join(str(v) for v in val)
    return str(val) if val else ""


def _flatten_papers(
    papers_detail: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Single-pass extraction of accepted and rejected gene rows.

    The pipeline stores ``report["genes"]`` as a summary dict (counts only)
    and has no top-level ``rejected_genes`` — both gene lists live inside
    ``papers_detail[i]``. Rejected entries are wrappers shaped
    ``{"gene": {...}, "reasons": [...]}``; unwrap them and synthesize a
    flat ``rejection_reason`` string.
    """
    genes: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for p in papers_detail:
        pmid = p.get("pmid", "")
        for g in p.get("genes", []):
            entry = dict(g)
            entry.setdefault("pmid", pmid)
            genes.append(entry)
        for rg in p.get("rejected_genes", []):
            # `or {}` covers both "gene" missing AND explicit null; plain
            # dict.get default only fires on missing.
            gene = dict(rg.get("gene") or {})
            gene.setdefault("pmid", pmid)
            reasons = rg.get("reasons", [])
            if isinstance(reasons, list):
                gene["rejection_reason"] = "; ".join(str(r) for r in reasons)
            else:
                gene["rejection_reason"] = str(reasons) if reasons else ""
            rejected.append(gene)
    return genes, rejected


def _find_report(project_root: str, report_id: str) -> Path | None:
    """Find a report JSON file by report_id.

    Lookup policy: exact match → prefix match (newest wins) → substring
    match (only when unique). Anchoring at stem-start prevents a short id
    like "test" from grabbing an unrelated "latest_test_notes".

    Symlinks are skipped: only regular files inside ``logs/json`` are
    candidates. Prevents partial-match resolution from leaving the project
    tree via a planted symlink.
    """
    logs_dir = (
        Path(project_root) / "logs" / "json" if project_root else Path("logs") / "json"
    )
    if not logs_dir.is_dir():
        return None

    exact = logs_dir / f"{report_id}.json"
    if exact.exists() and not exact.is_symlink():
        return exact

    json_files = [
        f for f in logs_dir.iterdir() if f.suffix == ".json" and not f.is_symlink()
    ]

    prefix_hits = [f for f in json_files if f.stem.startswith(report_id)]
    if prefix_hits:
        return max(prefix_hits, key=lambda x: x.stat().st_mtime)

    substring_hits = [f for f in json_files if report_id in f.stem]
    if len(substring_hits) == 1:
        return substring_hits[0]
    return None


def create_results_viewer_page(report_id: str, project_root: str) -> None:
    """Render the Results Viewer page for a given report ID."""
    if not _is_safe_report_id(report_id):
        ui.label("Invalid report ID.").classes("text-negative")
        ui.button(
            "Back to History",
            on_click=lambda: ui.navigate.to("/history"),
            icon="arrow_back",
        )
        return

    ui.label(f"Results: {report_id}").classes("page-title")

    report_path = _find_report(project_root, report_id)

    if report_path is None:
        ui.label(f"Report not found: {report_id}").classes("text-negative")
        ui.button(
            "Back to History",
            on_click=lambda: ui.navigate.to("/history"),
            icon="arrow_back",
        )
        return

    try:
        size = report_path.stat().st_size
        if size > _MAX_REPORT_SIZE:
            ui.label(
                f"Report too large to display "
                f"({size / 1024 / 1024:.1f} MB; "
                f"limit {_MAX_REPORT_SIZE / 1024 / 1024:.0f} MB)."
            ).classes("text-negative")
            return
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        ui.label(f"Error loading report: {e}").classes("text-negative")
        return

    papers_detail = report.get("papers_detail", [])
    genes_list, rejected_list = _flatten_papers(papers_detail)
    token_usage = report.get("token_usage", {})
    raw_genes = report.get("genes", {})
    genes_summary = raw_genes if isinstance(raw_genes, dict) else {}

    with ui.tabs().classes("w-full") as tabs:
        overview_tab = ui.tab("Overview")
        papers_tab = ui.tab("Papers")
        genes_tab = ui.tab("Genes")
        rejected_tab = ui.tab("Rejected")
        tokens_tab = ui.tab("Tokens")

    with ui.tab_panels(tabs, value=overview_tab).classes("w-full"):
        # ---- Overview ----
        with ui.tab_panel(overview_tab):
            ui.label("Overview").classes("section-header q-mb-md")
            papers_count = len(papers_detail)
            # Prefer the pipeline's own counts when present; fall back to
            # flattened-list lengths if the summary dict is missing.
            genes_count = genes_summary.get("validated", len(genes_list))
            rejected_count = genes_summary.get("rejected", len(rejected_list))

            with ui.row().classes("flex-wrap gap-md"):
                stat_card(papers_count, "Papers Processed", color="info")
                stat_card(genes_count, "Genes Extracted", color="secondary")
                stat_card(rejected_count, "Rejected Genes", color="negative")

                total_cost = token_usage.get("estimated_cost_usd", 0) or 0
                stat_card(f"${total_cost:.4f}", "Total Cost", color="warning")

                duration = report.get("total_processing_time", 0) or 0
                stat_card(
                    f"{duration:.1f}s" if duration < 60 else f"{duration / 60:.1f}m",
                    "Duration",
                    color="primary",
                )

                cache_hit = token_usage.get("cache_hit_rate", None)
                if cache_hit is not None:
                    stat_card(f"{cache_hit:.1%}", "Cache Hit Rate", color="info")

                bv_warnings = report.get("batch_validation_warnings", [])
                bv_count = len(bv_warnings) if isinstance(bv_warnings, list) else 0
                stat_card(bv_count, "Batch Warnings", color="warning")

            if genes_count > 0 or rejected_count > 0:
                with ui.row().classes("q-mt-lg gap-lg items-start"):
                    ui.echart(
                        {
                            "backgroundColor": "transparent",
                            "title": chart_title("Genes"),
                            "tooltip": {"trigger": "item"},
                            "series": [
                                {
                                    "type": "pie",
                                    "radius": ["45%", "70%"],
                                    "center": ["50%", "55%"],
                                    "data": [
                                        {
                                            "value": genes_count,
                                            "name": "Accepted",
                                        },
                                        {
                                            "value": rejected_count,
                                            "name": "Rejected",
                                        },
                                    ],
                                    "label": {"show": False},
                                    "itemStyle": {"borderRadius": 4},
                                }
                            ],
                            "color": [COLORS["secondary"], COLORS["negative"]],
                        }
                    ).classes("chart-container").style("width: 200px; height: 200px")

                    if genes_list:
                        buckets = [0] * 10
                        for g in genes_list:
                            # Bucket i covers [i/10, (i+1)/10); clamp a
                            # perfect 1.0 into the top bucket.
                            idx = min(int(_gene_confidence(g) * 10), 9)
                            buckets[idx] += 1
                        bucket_labels = [f"{i / 10:.1f}" for i in range(10)]
                        ui.echart(
                            {
                                "backgroundColor": "transparent",
                                "title": chart_title("Confidence"),
                                "tooltip": {"trigger": "axis"},
                                "xAxis": {
                                    "type": "category",
                                    "data": bucket_labels,
                                    "axisLabel": chart_axis_label(),
                                    "axisLine": {
                                        "lineStyle": {"color": COLORS["overlay"]},
                                    },
                                },
                                "yAxis": {
                                    "type": "value",
                                    "axisLabel": chart_axis_label(),
                                    "splitLine": chart_split_line(),
                                },
                                "series": [
                                    {
                                        "type": "bar",
                                        "data": buckets,
                                        "itemStyle": {
                                            "color": COLORS["primary"],
                                            "borderRadius": [3, 3, 0, 0],
                                        },
                                    }
                                ],
                            }
                        ).classes("chart-container").style(
                            "width: 320px; height: 200px"
                        )

        # ---- Papers ----
        with ui.tab_panel(papers_tab):
            ui.label("Papers").classes("section-header q-mb-md")
            if not papers_detail:
                empty_state(
                    "description",
                    "No papers in this report",
                    "This pipeline run did not process any papers.",
                )
            else:
                columns = [
                    {
                        "name": "pmid",
                        "label": "PMID",
                        "field": "pmid",
                        "sortable": True,
                    },
                    {
                        "name": "source",
                        "label": "Source",
                        "field": "source",
                        "sortable": True,
                    },
                    {
                        "name": "gene_count",
                        "label": "Genes",
                        "field": "gene_count",
                        "sortable": True,
                    },
                    {
                        "name": "processing_time",
                        "label": "Time (s)",
                        "field": "processing_time",
                        "sortable": True,
                    },
                    {
                        "name": "errors",
                        "label": "Errors",
                        "field": "errors",
                        "sortable": True,
                    },
                ]
                rows = []
                for i, p in enumerate(papers_detail):
                    pmid = p.get("pmid", "")
                    rows.append(
                        {
                            # Synthetic unique key — the same PMID can appear
                            # twice on a retry, which would collide under
                            # row_key="pmid" and break Quasar selection.
                            "row_id": f"{pmid}_{i}",
                            "pmid": pmid,
                            "source": p.get("source", ""),
                            "gene_count": p.get("gene_count", 0),
                            "processing_time": round(p.get("processing_time", 0), 2),
                            "errors": p.get("errors", ""),
                        }
                    )
                ui.table(
                    columns=columns,
                    rows=rows,
                    row_key="row_id",
                ).classes("w-full").props("filter")

        # ---- Genes ----
        with ui.tab_panel(genes_tab):
            ui.label("Genes Extracted").classes("section-header q-mb-md")
            if not genes_list:
                empty_state(
                    "biotech",
                    "No genes extracted",
                    "Nothing passed the confidence threshold for this run.",
                )
            else:
                columns = [
                    {
                        "name": "symbol",
                        "label": "Symbol",
                        "field": "symbol",
                        "sortable": True,
                    },
                    {
                        "name": "protein_name",
                        "label": "Protein",
                        "field": "protein_name",
                        "sortable": True,
                    },
                    {
                        "name": "gwas_trait",
                        "label": "GWAS Trait",
                        "field": "gwas_trait",
                        "sortable": True,
                    },
                    {
                        "name": "mendelian_randomization",
                        "label": "MR",
                        "field": "mendelian_randomization",
                        "sortable": True,
                    },
                    {
                        "name": "omics",
                        "label": "Omics",
                        "field": "omics",
                        "sortable": True,
                    },
                    {
                        "name": "confidence",
                        "label": "Confidence",
                        "field": "confidence",
                        "sortable": True,
                    },
                    {
                        "name": "pmid",
                        "label": "PMID",
                        "field": "pmid",
                        "sortable": True,
                    },
                ]
                rows = []
                for i, g in enumerate(genes_list):
                    symbol = _gene_symbol(g)
                    pmid = g.get("pmid", "")
                    rows.append(
                        {
                            "row_id": f"{symbol}_{pmid}_{i}",
                            "symbol": symbol,
                            "protein_name": g.get("protein_name", "") or "",
                            "gwas_trait": _join_field(g, "gwas_trait"),
                            "mendelian_randomization": (
                                "Yes" if g.get("mendelian_randomization") else "No"
                            ),
                            "omics": _join_field(
                                g,
                                "omics_evidence",
                                "evidence_from_other_omics_studies",
                            ),
                            "confidence": _gene_confidence(g),
                            "pmid": pmid,
                        }
                    )
                ui.table(
                    columns=columns,
                    rows=rows,
                    row_key="row_id",
                ).classes("w-full").props("filter")

        # ---- Rejected Genes ----
        with ui.tab_panel(rejected_tab):
            ui.label("Rejected Genes").classes("section-header q-mb-md")
            if not rejected_list:
                empty_state(
                    "check_circle",
                    "No rejections",
                    "Every extracted gene passed validation for this run.",
                )
            else:
                columns = [
                    {
                        "name": "symbol",
                        "label": "Symbol",
                        "field": "symbol",
                        "sortable": True,
                    },
                    {
                        "name": "confidence",
                        "label": "Confidence",
                        "field": "confidence",
                        "sortable": True,
                    },
                    {
                        "name": "reason",
                        "label": "Rejection Reason",
                        "field": "reason",
                        "sortable": True,
                    },
                ]
                rows = []
                for i, g in enumerate(rejected_list):
                    symbol = _gene_symbol(g)
                    rows.append(
                        {
                            "row_id": f"{symbol}_{i}",
                            "symbol": symbol,
                            "confidence": _gene_confidence(g),
                            "reason": g.get("rejection_reason", g.get("reason", "")),
                        }
                    )
                ui.table(
                    columns=columns,
                    rows=rows,
                    row_key="row_id",
                ).classes("w-full")

        # ---- Tokens ----
        with ui.tab_panel(tokens_tab):
            ui.label("Token Usage").classes("section-header q-mb-md")
            if not token_usage:
                ui.label("No token usage data.").classes("text-muted")
            else:
                input_tok = token_usage.get("input_tokens", 0)
                output_tok = token_usage.get("output_tokens", 0)
                thinking_tok = token_usage.get("thinking_tokens", 0)
                cache_read_tok = token_usage.get("cache_read_input_tokens", 0)
                cache_create_tok = token_usage.get("cache_creation_input_tokens", 0)
                total_cost = token_usage.get("estimated_cost_usd", 0) or 0

                with ui.row().classes("flex-wrap gap-md"):
                    stat_card(f"{input_tok:,}", "Input Tokens", color="primary")
                    stat_card(f"{output_tok:,}", "Output Tokens", color="secondary")
                    stat_card(f"{thinking_tok:,}", "Thinking Tokens", color="info")
                    stat_card(f"{cache_read_tok:,}", "Cache Read", color="secondary")
                    stat_card(f"{cache_create_tok:,}", "Cache Creation", color="info")
                    stat_card(f"${total_cost:.4f}", "Total Cost", color="warning")

                token_data = [
                    v
                    for v in [
                        {"value": input_tok, "name": "Input"} if input_tok else None,
                        {"value": output_tok, "name": "Output"} if output_tok else None,
                        {"value": thinking_tok, "name": "Thinking"}
                        if thinking_tok
                        else None,
                        {"value": cache_read_tok, "name": "Cache Read"}
                        if cache_read_tok
                        else None,
                        {"value": cache_create_tok, "name": "Cache Create"}
                        if cache_create_tok
                        else None,
                    ]
                    if v is not None
                ]
                if token_data:
                    with ui.row().classes("q-mt-lg"):
                        ui.echart(
                            {
                                "backgroundColor": "transparent",
                                "title": chart_title("Token Breakdown"),
                                "tooltip": {
                                    "trigger": "item",
                                    "formatter": "{b}: {c} ({d}%)",
                                },
                                "legend": {
                                    "bottom": "0%",
                                    "textStyle": {
                                        "color": COLORS["text_secondary"],
                                    },
                                },
                                "series": [
                                    {
                                        "type": "pie",
                                        "radius": ["40%", "65%"],
                                        "center": ["50%", "48%"],
                                        "data": token_data,
                                        "label": {"show": False},
                                        "itemStyle": {"borderRadius": 4},
                                    }
                                ],
                                "color": CHART_ACCENT_COLORS,
                            }
                        ).classes("chart-container").style(
                            "width: 360px; height: 280px"
                        )

    ui.separator().classes("nav-separator q-my-md")
    with ui.row().classes("items-center gap-sm"):
        ui.button(
            "Back to History",
            on_click=lambda: ui.navigate.to("/history"),
            icon="arrow_back",
        ).props("flat").classes("theme-btn-ghost")
        ui.label(f"File: {report_path.name}").classes("text-muted self-center")
