"""Configure & Run page for the HPC vLLM stack."""

from __future__ import annotations

import asyncio
import dataclasses
import os
import shutil
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

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
from pipeline_app_hpc.components.preset_selector import create_preset_selector
from pipeline_app_hpc.config import (
    PROMPT_VERSIONS,
    HpcAppConfig,
    add_run_to_history,
    load_env_secrets,
    save_config,
    strip_secrets_from_config,
)
from pipeline_app_hpc.pages._helpers import get_project_anchor, wire_runner_to_panel
from pipeline_app_hpc.runner import (
    PIPELINE_STAGES,
    PipelineRunner,
    SubprocessLock,
)

if TYPE_CHECKING:
    from pipeline_app_hpc.hpc.lifecycle import VllmServer


def _python_fallback_dir(current: str) -> Path | None:
    """Resolve a bare name like 'python3' to its parent directory via PATH."""
    if not current or os.sep in current:
        return None
    resolved = shutil.which(current)
    return Path(resolved).parent if resolved else None


# vLLM quantization options
VLLM_QUANTIZATION_OPTIONS: dict[str, str] = {
    "bitsandbytes": "bitsandbytes (BnB 4-bit)",
    "gptq": "GPTQ",
    "awq": "AWQ",
    "": "None (fp16/bf16)",
}


def create_configure_run_page(
    lock: SubprocessLock,
    config: HpcAppConfig,
    runner: PipelineRunner,
    vllm_server: VllmServer,
) -> None:
    """Render the Configure & Run page."""
    ui.label("Configure & Run").classes("page-title")

    run_status_label: list[ui.label] = []
    panel_ref: list[ExecutionPanel] = []
    run_btn_ref: list[ui.button] = []

    def _refresh_stage_tracker() -> None:
        if panel_ref:
            panel_ref[0].refresh(runner.stage_statuses)

    # ---- HPC card (vLLM state) at top ----
    render_hpc_card(vllm_server)

    with ui.splitter(value=40).classes("w-full") as splitter:
        with splitter.before, ui.card().classes("w-full q-pa-md theme-card"):
            # ---- Presets ----
            create_preset_selector(config)

            ui.separator().classes("nav-separator")

            # ---- Run Mode (local PDFs only) ----
            ui.label("Run Mode: Local PDFs").classes("section-header")

            bound_path_input(
                config,
                "local_pdfs_path",
                label="Local PDFs Path",
                on_pick=lambda inp: _pick_local_pdfs(inp),
            )
            ui.checkbox("Skip Validation").bind_value(config, "skip_validation")

            async def _pick_local_pdfs(inp: ui.input) -> None:
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

            ui.separator().classes("nav-separator")

            # ---- vLLM Settings ----
            ui.label("vLLM Settings").classes("section-header")

            bound_input(
                config,
                "vllm_base_model",
                label="Base Model (HF repo or local path)",
            )
            bound_input(
                config,
                "vllm_adapter_path",
                label="Adapter Path (leave empty for base model)",
            )
            bound_input(
                config,
                "vllm_adapter_name",
                label="Adapter Name (LoRA slot name)",
            )
            bound_number(
                config,
                "vllm_max_model_len",
                label="Max Model Length (tokens)",
                min=1024,
                max=131_072,
                step=1024,
            )
            bound_number(
                config,
                "vllm_max_lora_rank",
                label="Max LoRA Rank",
                min=1,
                max=256,
            )
            bound_select(
                config,
                "vllm_quantization",
                options=VLLM_QUANTIZATION_OPTIONS,
                label="Quantization",
            )
            bound_select(
                config,
                "prompt_version",
                options={v: v for v in PROMPT_VERSIONS},
                label="Prompt Version",
            )

            ui.separator().classes("nav-separator")

            # ---- SSH & HPC Paths ----
            ui.label("SSH & HPC Paths").classes("section-header")

            bound_input(config, "ssh_alias", label="SSH Alias")
            bound_number(
                config,
                "vllm_local_port",
                label="Local Port (SSH tunnel)",
                min=1024,
                max=65535,
            )
            bound_input(config, "vllm_remote_workdir", label="Remote Work Dir")
            bound_input(config, "vllm_remote_log_dir", label="Remote Log Dir")
            bound_input(config, "vllm_remote_venv_path", label="Remote venv Path")
            bound_input(config, "vllm_hf_home", label="HF_HOME on HPC")

            ui.separator().classes("nav-separator")

            # ---- SLURM ----
            ui.label("SLURM").classes("section-header")

            bound_input(config, "vllm_account", label="Account")
            bound_input(config, "vllm_partition", label="Partition")
            bound_input(config, "vllm_qos", label="QoS")
            bound_input(config, "vllm_time_limit", label="Time Limit (HH:MM:SS)")
            bound_number(
                config,
                "vllm_cpus_per_task",
                label="CPUs per Task",
                min=1,
                max=128,
            )
            bound_input(config, "vllm_mem", label="Memory (e.g. 64G)")
            bound_number(
                config,
                "vllm_readiness_timeout",
                label="Readiness Timeout (s)",
                min=60.0,
                max=3600.0,
                step=30.0,
                format="%.0f",
            )

            ui.separator().classes("nav-separator")

            # ---- Concurrency ----
            ui.label("Concurrency").classes("section-header")
            bound_number(
                config,
                "max_concurrent_papers",
                label="Max Concurrent Papers",
                min=1,
                max=50,
            )
            bound_number(
                config,
                "rpm_limit",
                label="RPM Limit",
                min=1,
            )
            bound_number(
                config,
                "tpm_limit",
                label="TPM Limit",
                min=1000,
            )
            bound_number(
                config,
                "estimated_tokens_per_call",
                label="Estimated Tokens Per Call",
                min=1000,
            )

            ui.separator().classes("nav-separator")

            # ---- Retry Settings ----
            ui.label("Retry Settings").classes("section-header")
            bound_number(
                config,
                "max_retries",
                label="Max Retries",
                min=0,
            )
            bound_number(
                config,
                "max_rate_limit_retries",
                label="Max Rate Limit Retries",
                min=0,
            )
            bound_number(
                config,
                "rate_limit_retry_delay",
                label="Rate Limit Retry Delay (s)",
                min=0.0,
                step=0.5,
                format="%.1f",
            )

            ui.separator().classes("nav-separator")

            # ---- Environment ----
            ui.label("Environment").classes("section-header")
            bound_path_input(
                config,
                "python_path",
                label="Python Path",
                on_pick=lambda inp: _pick_python_path(inp),
            )
            bound_path_input(
                config,
                "project_root",
                label="Project Root",
                on_pick=lambda inp: _pick_project_root(inp),
            )
            bound_path_input(
                config,
                "progress_file",
                label="Progress File (optional)",
                on_pick=lambda inp: _pick_progress_file(inp),
            )

            async def _pick_python_path(inp: ui.input) -> None:
                current = inp.value or "python3"
                result = await pick_path(
                    mode="file",
                    anchor=None,
                    current_value=current,
                    fallback_start=_python_fallback_dir(current) or Path.home(),
                    symlinks="preserve",
                    title="Select Python interpreter",
                )
                if result is not None:
                    inp.value = result

            async def _pick_project_root(inp: ui.input) -> None:
                current = inp.value or ""
                result = await pick_path(
                    mode="directory",
                    anchor=None,
                    current_value=current,
                    fallback_start=Path(os.getcwd()),
                    title="Select Project Root",
                )
                if result is not None:
                    inp.value = result

            async def _pick_progress_file(inp: ui.input) -> None:
                anchor = get_project_anchor(config)
                if anchor is None:
                    ui.notify("Set Project Root first", color="warning")
                    return
                current = inp.value or ""
                default_filename = Path(current).name if current else "progress.json"
                result = await pick_path(
                    mode="directory",
                    anchor=anchor,
                    current_value=current,
                    save_as=True,
                    default_filename=default_filename,
                    title="Choose folder + filename for progress file",
                )
                if result is not None:
                    inp.value = result

            ui.separator().classes("nav-separator")

            def _save_settings() -> None:
                save_config(config)
                if vllm_server.update_config(config):
                    ui.notify("Settings saved", color="positive")
                else:
                    ui.notify(
                        "Settings saved; restart vLLM to use SSH/HPC changes",
                        color="warning",
                    )

            ui.button(
                "Save Settings",
                on_click=_save_settings,
                icon="save",
            ).props("unelevated").classes("w-full btn-primary")

        with splitter.after, ui.card().classes("w-full q-pa-md theme-card"):
            ui.label("Execution").classes("section-header q-mb-sm")

            panel = ExecutionPanel(PIPELINE_STAGES, runner.stage_statuses)
            panel_ref.append(panel)

            ui.button(
                "Refresh",
                on_click=_refresh_stage_tracker,
                icon="refresh",
            ).props("outline").classes("btn-secondary q-mb-sm")

            status_lbl = ui.label("").classes("text-muted q-mb-sm")
            run_status_label.append(status_lbl)

            def _set_run_status(
                text: str,
                state: Literal["neutral", "positive", "negative"] = "neutral",
            ) -> None:
                with suppress(RuntimeError):
                    if not run_status_label:
                        return
                    lbl = run_status_label[0]
                    lbl.text = text
                    tone_class = {
                        "neutral": "text-muted",
                        "positive": "text-positive",
                        "negative": "text-negative",
                    }[state]
                    lbl.classes(
                        remove="text-muted text-positive text-negative",
                        add=tone_class,
                    )

            wire_runner_to_panel(runner, panel, _refresh_stage_tracker)

            # Reconnect path: page may be rendering mid-run (or after the run
            # already finished). Replay buffered lines via load_batch so the
            # DOM paints in one cycle instead of N × 50 ms flush ticks, then
            # re-sync the tracker from runner.stage_statuses.
            if runner.log_lines:
                # HPC runner stores plain strings; ExecutionPanel.replay
                # expects (type, line) tuples — wrap each line as "out".
                panel.replay(("out", ln) for ln in runner.log_lines)
            _refresh_stage_tracker()
            if lock.is_running:
                _set_run_status("Running...", "neutral")
            elif runner.last_result is not None:
                # Run finished while the user was on another page.
                last = runner.last_result
                last_status = "success" if last.exit_code == 0 else "failed"
                _set_run_status(
                    f"Finished: {last_status} (exit {last.exit_code})",
                    "positive" if last_status == "success" else "negative",
                )

            async def _run_pipeline() -> None:
                # Guard before try/finally so the early-return path doesn't
                # re-enable the button while another click's run is active.
                if lock.is_running:
                    ui.notify(
                        "A process is already running",
                        color="warning",
                    )
                    return

                if run_btn_ref:
                    run_btn_ref[0].disable()

                # Repaint immediately for UX; runner.run resets state under
                # the lock via the on_locked hook so a concurrent tab can't
                # corrupt the in-flight run's tracker.
                # Suppress RuntimeError: a client that disconnected between
                # disable() and refresh would otherwise abort here, leaving
                # the button permanently disabled (finally never runs).
                with suppress(RuntimeError):
                    _refresh_stage_tracker()

                try:
                    if panel_ref:
                        panel_ref[0].clear_log()

                    started_at = datetime.now(UTC)

                    snap = vllm_server.snapshot
                    if not snap.is_ready:
                        ui.notify(
                            f"vLLM is not ready (state={snap.state.name}). "
                            "Click Start vLLM first.",
                            color="warning",
                        )
                        return

                    # Status set after credential check so an early return
                    # doesn't leave "Running..." displayed.
                    _set_run_status("Running...", "neutral")

                    fresh_secrets = load_env_secrets(
                        config.project_root, use_cache=False
                    )

                    try:
                        result = await runner.run(
                            config=config,
                            secrets=fresh_secrets,
                        )
                    except Exception as exc:
                        # RuntimeError here is "already running" from the
                        # lock's race-close check — warning color vs the
                        # negative for the validation/unknown cases.
                        match exc:
                            case RuntimeError():
                                notify_color, notify_msg = "warning", str(exc)
                                status_text = str(exc)
                            case ValueError():
                                notify_color, notify_msg = "negative", str(exc)
                                status_text = f"Failed: {exc}"
                            case _:
                                notify_color = "negative"
                                notify_msg = f"Unexpected error: {exc}"
                                status_text = f"Error: {exc}"
                        with suppress(RuntimeError):
                            ui.notify(notify_msg, color=notify_color)
                        _set_run_status(status_text, "negative")
                        _refresh_stage_tracker()
                        return

                    # Terminal stage bookkeeping happens inside runner.run();
                    # just repaint the tracker with the settled statuses.
                    _refresh_stage_tracker()

                    status = "success" if result.exit_code == 0 else "failed"
                    _set_run_status(
                        f"Finished: {status} (exit {result.exit_code})",
                        "positive" if status == "success" else "negative",
                    )

                    # Store the stem so it passes the Results page safe-ID
                    # regex (a raw path would contain slashes).
                    run_id = (
                        Path(result.report_path).stem
                        if result.report_path
                        else started_at.isoformat()
                    )
                    run_record = {
                        "id": run_id,
                        "started_at": started_at.isoformat(),
                        "run_mode": config.run_mode,
                        "status": status,
                        "exit_code": result.exit_code,
                        "report_path": result.report_path,
                        "config": strip_secrets_from_config(dataclasses.asdict(config)),
                    }
                    await asyncio.to_thread(add_run_to_history, run_record)
                    with suppress(RuntimeError):
                        ui.notify(
                            f"Run {status}",
                            color=("positive" if status == "success" else "negative"),
                        )
                finally:
                    with suppress(RuntimeError):
                        if run_btn_ref:
                            run_btn_ref[0].enable()

            run_btn = (
                ui.button(
                    "Run Pipeline",
                    on_click=_run_pipeline,
                    icon="play_arrow",
                )
                .props("unelevated size=lg")
                .classes("w-full q-mt-md btn-execute")
            )
            run_btn_ref.append(run_btn)
