"""Tuning Config & Run page — form and stage execution panel (vLLM only)."""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING

from nicegui import ui
from pipeline_app.components.execution_panel import ExecutionPanel
from pipeline_app.components.form_fields import (
    bound_input,
    bound_number,
    bound_path_input,
    bound_select,
)
from pipeline_app.components.path_picker import pick_path

from pipeline_app_hpc.components.hpc_card import render_hpc_card
from pipeline_app_hpc.config import (
    PROMPT_VERSIONS,
    HpcAppConfig,
    TuningConfig,
    load_env_secrets,
    save_tuning_config,
)
from pipeline_app_hpc.pages._helpers import get_project_anchor, wire_runner_to_panel
from pipeline_app_hpc.runner import (
    TUNING_STAGES,
    SubprocessLock,
    TuningRunner,
    build_tuning_extract_config,
    get_tuning_project_root,
)

if TYPE_CHECKING:
    from pipeline_app_hpc.hpc.lifecycle import VllmServer


def create_tuning_page(
    lock: SubprocessLock,
    config: HpcAppConfig,
    tuning_config: TuningConfig,
    tuning_runner: TuningRunner,
    vllm_server: VllmServer,
) -> None:
    """Render the Tuning Config & Run page."""
    ui.label("Tuning").classes("page-title")

    panel_ref: list[ExecutionPanel] = []

    def _refresh_stage_tracker() -> None:
        if panel_ref:
            panel_ref[0].refresh(tuning_runner.stage_statuses)

    # ---- HPC card (vLLM state) at top ----
    render_hpc_card(vllm_server)

    with ui.splitter(value=40).classes("w-full") as splitter:
        with splitter.before, ui.card().classes("w-full q-pa-md theme-card"):
            ui.label("Tuning Configuration").classes("section-header q-mb-sm")

            bound_path_input(
                tuning_config,
                "pdf_path",
                label="PDF Path",
                on_pick=lambda inp: _pick_pdf_path(inp),
            )

            bound_path_input(
                tuning_config,
                "gold_standard_path",
                label="Gold Standard Path",
                on_pick=lambda inp: _pick_gold_standard(inp),
            )

            async def _pick_pdf_path(inp: ui.input) -> None:
                anchor = get_project_anchor(config)
                if anchor is None:
                    ui.notify("Set Project Root first", color="warning")
                    return
                result = await pick_path(
                    mode="file",
                    anchor=anchor,
                    current_value=inp.value or "",
                    extensions=frozenset({".pdf"}),
                    allow_directories_as_files=True,
                    title="Select PDF file or folder of PDFs",
                )
                if result is not None:
                    inp.value = result

            async def _pick_gold_standard(inp: ui.input) -> None:
                anchor = get_project_anchor(config)
                if anchor is None:
                    ui.notify("Set Project Root first", color="warning")
                    return
                result = await pick_path(
                    mode="file",
                    anchor=anchor,
                    current_value=inp.value or "",
                    extensions=frozenset({".csv"}),
                    title="Select gold standard CSV",
                )
                if result is not None:
                    inp.value = result

            bound_number(
                tuning_config,
                "confidence_threshold",
                label="Confidence Threshold",
                min=0.0,
                max=1.0,
                step=0.01,
                format="%.2f",
            )

            bound_number(
                tuning_config,
                "repeats",
                label="Repeats",
                min=1,
                max=20,
            )

            ui.checkbox("Auto-Advance Stages").bind_value(tuning_config, "auto_advance")

            bound_number(
                tuning_config,
                "f_beta_weight",
                label="F-Beta Weight",
                min=0.5,
                max=5.0,
                step=0.1,
                format="%.1f",
            )
            with ui.element("div").classes("theme-note theme-note-info q-mt-xs"):
                f_beta_help = (
                    "Beta (β) is the weight parameter that controls how"
                    " much more recall matters relative to precision."
                    " F-beta is the score computed from that weight — the"
                    " weighted harmonic mean of precision and recall:"
                    " F_β = (1+β²)·P·R / (β²·P+R).",
                    "β=1 (F1): precision and recall weighted equally."
                    " β=2 (F2, default): recall weighted 2× more than"
                    " precision. β=0.5 (F0.5): precision weighted 2×"
                    " more than recall.",
                    "The default β=2 favors gene discovery because"
                    " missing a real causal gene (low recall) is harder"
                    " to catch than including a spurious one (low"
                    " precision), which downstream review can filter out.",
                )
                for i, text in enumerate(f_beta_help):
                    cls = "text-caption"
                    if i > 0:
                        cls += " q-mt-xs"
                    ui.label(text).classes(cls)

            ui.textarea(
                label="Notes",
                value=tuning_config.notes,
            ).classes("w-full").bind_value(tuning_config, "notes")

            ui.separator().classes("nav-separator")
            ui.label("vLLM Settings").classes("section-header")

            ui.checkbox("Use Main Config vLLM Settings").bind_value(
                tuning_config, "use_main_config"
            )
            with ui.column().classes("w-full") as vllm_override_fields:
                bound_input(tuning_config, "python_path", label="Python Path")
                bound_input(tuning_config, "project_root", label="Project Root")
                bound_input(
                    tuning_config,
                    "vllm_base_model",
                    label="Base Model (HF repo or local path)",
                )
                bound_input(
                    tuning_config,
                    "vllm_adapter_path",
                    label="Adapter Path (leave empty for base model)",
                )
                bound_input(
                    tuning_config,
                    "vllm_adapter_name",
                    label="Adapter Name (LoRA slot name)",
                )
                bound_number(
                    tuning_config,
                    "vllm_max_model_len",
                    label="Max Model Length (tokens)",
                    min=1024,
                    max=131_072,
                    step=1024,
                )
                bound_select(
                    tuning_config,
                    "prompt_version",
                    options={v: v for v in PROMPT_VERSIONS},
                    label="Prompt Version",
                )
            vllm_override_fields.bind_visibility_from(
                tuning_config, "use_main_config", backward=lambda v: not v
            )

            ui.separator().classes("nav-separator")

            def _save_tuning_settings() -> None:
                save_tuning_config(tuning_config)
                target_config = (
                    config
                    if tuning_config.use_main_config
                    else build_tuning_extract_config(config, tuning_config)
                )
                if vllm_server.update_config(target_config):
                    ui.notify("Tuning settings saved", color="positive")
                else:
                    ui.notify(
                        "Tuning settings saved; restart vLLM to use vLLM changes",
                        color="warning",
                    )

            ui.button(
                "Save Settings",
                on_click=_save_tuning_settings,
                icon="save",
            ).props("unelevated").classes("w-full btn-primary")

        with splitter.after, ui.card().classes("w-full q-pa-md theme-card"):
            ui.label("Execution").classes("section-header q-mb-sm")

            panel = ExecutionPanel(
                TUNING_STAGES,
                tuning_runner.stage_statuses,
            )
            panel_ref.append(panel)

            ui.button(
                "Refresh",
                on_click=_refresh_stage_tracker,
                icon="refresh",
            ).props("outline").classes("btn-secondary q-mb-sm")

            # Stage selector for targeted re-runs
            report_path_ref: list[str] = []

            with ui.row().classes("w-full gap-sm items-center q-mb-sm"):
                stage_sel = ui.select(
                    options=TUNING_STAGES,
                    value=TUNING_STAGES[0],
                    label="Stage",
                ).classes("flex-1")

            run_btn_ref: list[ui.button] = []

            run_btn = (
                ui.button(
                    "Run Stage",
                    icon="play_arrow",
                )
                .props("unelevated size=lg")
                .classes("w-full q-mt-md btn-execute")
            )
            run_btn_ref.append(run_btn)

            cancel_btn = (
                ui.button(
                    "Cancel",
                    on_click=lambda: tuning_runner.cancel(),
                    icon="stop",
                )
                .props("outline")
                .classes("w-full q-mt-xs btn-warning")
            )
            cancel_btn.set_visibility(False)

            # Reconnect path: show live stage status.
            if tuning_runner.log_lines:
                # HPC runner stores plain strings; ExecutionPanel.replay
                # expects (type, line) tuples — wrap each line as "out".
                panel.replay(("out", ln) for ln in tuning_runner.log_lines)
                _refresh_stage_tracker()
            if tuning_runner.any_running:
                run_btn.disable()
                cancel_btn.set_visibility(True)

            wire_runner_to_panel(tuning_runner, panel, _refresh_stage_tracker)

            async def _run_stage() -> None:
                if tuning_runner.any_running:
                    ui.notify("A process is already running", color="warning")
                    return

                stage = stage_sel.value or TUNING_STAGES[0]

                if stage == "extract":
                    snap = vllm_server.snapshot
                    if not snap.is_ready:
                        ui.notify(
                            f"vLLM is not ready (state={snap.state.name}). "
                            "Click Start vLLM first.",
                            color="warning",
                        )
                        return

                with suppress(RuntimeError):
                    run_btn.disable()
                    cancel_btn.set_visibility(True)

                # Skip reset when another tab holds the lock — its in-flight
                # run owns the shared state. TuningRunner doesn't have an
                # on_locked hook because auto-advance must preserve completed
                # stages' history across stage runs.
                if not lock.is_running:
                    tuning_runner.reset_state()
                # Suppress RuntimeError: a client that disconnected between
                # the disable() above and these UI mutations would otherwise
                # abort here, leaving run_btn permanently disabled (the
                # finally below only fires once the try is entered).
                if panel_ref:
                    with suppress(RuntimeError):
                        panel_ref[0].clear_log()
                        _refresh_stage_tracker()

                try:
                    fresh_secrets = load_env_secrets(
                        get_tuning_project_root(config, tuning_config),
                        use_cache=False,
                    )
                    current_stage = stage
                    while True:
                        report_path = report_path_ref[0] if report_path_ref else None
                        result = await tuning_runner.run_stage(
                            stage=current_stage,
                            config=config,
                            tuning_config=tuning_config,
                            secrets=fresh_secrets,
                            report_path=report_path,
                        )
                        _refresh_stage_tracker()

                        if result.exit_code != 0:
                            with suppress(RuntimeError):
                                ui.notify(
                                    "Stage "
                                    f"'{current_stage}' failed "
                                    f"(exit {result.exit_code})",
                                    color="negative",
                                )
                            break

                        if result.report_path:
                            report_path_ref.clear()
                            report_path_ref.append(result.report_path)
                        with suppress(RuntimeError):
                            ui.notify(
                                f"Stage '{current_stage}' complete",
                                color="positive",
                            )

                        idx = TUNING_STAGES.index(current_stage)
                        if not tuning_config.auto_advance or idx + 1 >= len(
                            TUNING_STAGES
                        ):
                            break
                        current_stage = TUNING_STAGES[idx + 1]
                        with suppress(RuntimeError):
                            stage_sel.set_value(current_stage)

                except RuntimeError as e:
                    # "Already running" from the lock's race-close check;
                    # match configure_run.py's tone (warning, not negative).
                    with suppress(RuntimeError):
                        ui.notify(str(e), color="warning")
                except Exception as e:
                    with suppress(RuntimeError):
                        ui.notify(f"Error: {e}", color="negative")
                finally:
                    with suppress(RuntimeError):
                        run_btn.enable()
                    with suppress(RuntimeError):
                        cancel_btn.set_visibility(False)
                    with suppress(RuntimeError):
                        _refresh_stage_tracker()

            run_btn.on_click(_run_stage)
