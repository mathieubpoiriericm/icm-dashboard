"""Configure & Run page — config form and live execution panel."""

from __future__ import annotations

import dataclasses
from datetime import datetime
from pathlib import Path

from nicegui import ui

from pipeline_app.components.confirm_dialog import confirm
from pipeline_app.components.log_viewer import LogViewer
from pipeline_app.components.preset_dialog import prompt_preset_name
from pipeline_app.components.stage_tracker import create_stage_tracker
from pipeline_app.config import (
    LLM_EFFORTS,
    LLM_MODELS,
    PROMPT_VERSIONS,
    EnvSecrets,
    PipelineAppConfig,
    add_run_to_history,
    delete_preset,
    load_env_secrets,
    load_preset,
    load_presets,
    save_config,
    save_preset,
    strip_secrets_from_config,
)
from pipeline_app.runner import (
    PIPELINE_STAGES,
    PipelineRunner,
    SubprocessLock,
)

RUN_MODES = {
    "standard": "Standard",
    "local_pdfs": "Local PDFs",
    "pmid_list": "PMID List",
}


def create_configure_run_page(
    lock: SubprocessLock,
    config: PipelineAppConfig,
    secrets: EnvSecrets,
) -> None:
    """Render the Configure & Run page."""
    ui.label("Configure & Run").classes("page-title")

    stage_statuses: dict[str, str] = {s: "pending" for s in PIPELINE_STAGES}
    run_status_label: list[ui.label] = []
    stage_container: list[ui.element] = []
    log_viewer_ref: list[LogViewer] = []
    run_btn_ref: list[ui.button] = []
    runner = PipelineRunner(lock)

    def _refresh_stage_tracker() -> None:
        if stage_container:
            stage_container[0].clear()
            with stage_container[0]:
                create_stage_tracker(PIPELINE_STAGES, stage_statuses)

    with ui.splitter(value=40).classes("w-full") as splitter:
        with splitter.before, ui.card().classes("w-full q-pa-md theme-card"):
            # ---- Presets ----
            ui.label("Presets").classes("section-header")
            presets = load_presets()
            preset_options = {p.id: p.name for p in presets}
            preset_select = ui.select(
                options=preset_options,
                label="Preset",
                value=None,
            ).classes("w-full")

            with ui.row().classes("q-mb-md gap-sm"):
                ui.button(
                    "Load",
                    on_click=lambda: _load_preset(preset_select.value),
                    icon="download",
                ).props("flat size=sm").classes("theme-btn-ghost preset-action-btn")
                ui.button(
                    "Save",
                    on_click=lambda: _save_current_preset(),
                    icon="save",
                ).props("flat size=sm").classes("theme-btn-ghost preset-action-btn")
                ui.button(
                    "Delete",
                    on_click=lambda: _delete_preset(preset_select.value),
                    icon="delete",
                    color="negative",
                ).props("flat size=sm").classes("theme-btn-ghost preset-action-btn")

            def _load_preset(preset_id: str | None) -> None:
                if not preset_id:
                    ui.notify("No preset selected", color="warning")
                    return
                loaded = load_preset(preset_id)
                if loaded is None:
                    ui.notify("Preset not found", color="negative")
                    return
                for field in dataclasses.fields(loaded):
                    setattr(config, field.name, getattr(loaded, field.name))
                ui.notify("Preset loaded", color="positive")

            async def _save_current_preset() -> None:
                result = await prompt_preset_name()
                if result:
                    updated = save_preset(result, config)
                    ui.notify(f"Saved preset: {result}", color="positive")
                    preset_select.options = {p.id: p.name for p in updated}
                    # Select the newly saved preset so Delete works on it
                    # immediately without an extra dropdown click.
                    if updated:
                        preset_select.value = updated[-1].id
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
                preset_select.options = {p.id: p.name for p in updated}
                preset_select.value = None
                preset_select.update()

            ui.separator().classes("nav-separator")

            # ---- Run Mode ----
            ui.label("Run Mode").classes("section-header")
            run_mode_select = (
                ui.select(
                    options=RUN_MODES,
                    label="Mode",
                    value=config.run_mode,
                )
                .classes("w-full")
                .bind_value(config, "run_mode")
            )

            with ui.column().classes("w-full") as standard_fields:
                ui.number(
                    label="Days Back",
                    value=config.days_back,
                    min=1,
                    max=365,
                ).classes("w-full").bind_value(config, "days_back")
                ui.checkbox("Dry Run").bind_value(config, "dry_run")
                ui.checkbox("Test Mode").bind_value(config, "test_mode")
                ui.checkbox("Sync External Data").bind_value(
                    config, "sync_external_data"
                )

            with ui.column().classes("w-full") as local_pdfs_fields:
                ui.input(
                    label="Local PDFs Path",
                    value=config.local_pdfs_path,
                ).classes("w-full").bind_value(config, "local_pdfs_path")
                ui.checkbox("Skip Validation").bind_value(config, "skip_validation")

            with ui.column().classes("w-full") as pmid_list_fields:
                ui.input(
                    label="PMIDs File Path",
                    value=config.pmids_path,
                ).classes("w-full").bind_value(config, "pmids_path")
                ui.checkbox("Skip Validation").bind_value(config, "skip_validation")

            def _update_run_mode_fields() -> None:
                mode = config.run_mode
                standard_fields.set_visibility(mode == "standard")
                local_pdfs_fields.set_visibility(mode == "local_pdfs")
                pmid_list_fields.set_visibility(mode == "pmid_list")

            run_mode_select.on(
                "update:model-value",
                lambda _: _update_run_mode_fields(),
            )
            _update_run_mode_fields()

            ui.separator().classes("nav-separator")

            # ---- LLM Settings ----
            ui.label("LLM Settings").classes("section-header")
            ui.select(
                options=LLM_MODELS,
                label="Model",
                value=config.llm_model,
            ).classes("w-full").bind_value(config, "llm_model")
            ui.select(
                options=LLM_EFFORTS,
                label="Effort",
                value=config.llm_effort,
            ).classes("w-full").bind_value(config, "llm_effort")
            ui.number(
                label="Max Tokens (0 = default)",
                value=config.llm_max_tokens,
                min=0,
            ).classes("w-full").bind_value(config, "llm_max_tokens")
            ui.select(
                options=PROMPT_VERSIONS,
                label="Prompt Version",
                value=config.prompt_version,
            ).classes("w-full").bind_value(config, "prompt_version")
            ui.number(
                label="Confidence Threshold",
                value=config.confidence_threshold,
                min=0.0,
                max=1.0,
                step=0.01,
                format="%.2f",
            ).classes("w-full").bind_value(config, "confidence_threshold")
            ui.number(
                label="Max Paper Text Chars",
                value=config.max_paper_text_chars,
                min=1000,
            ).classes("w-full").bind_value(config, "max_paper_text_chars")

            ui.separator().classes("nav-separator")

            # ---- Concurrency ----
            ui.label("Concurrency").classes("section-header")
            ui.number(
                label="Max Concurrent Papers",
                value=config.max_concurrent_papers,
                min=1,
                max=50,
            ).classes("w-full").bind_value(config, "max_concurrent_papers")
            ui.number(
                label="RPM Limit",
                value=config.rpm_limit,
                min=1,
            ).classes("w-full").bind_value(config, "rpm_limit")
            ui.number(
                label="TPM Limit",
                value=config.tpm_limit,
                min=1000,
            ).classes("w-full").bind_value(config, "tpm_limit")
            ui.number(
                label="Estimated Tokens Per Call",
                value=config.estimated_tokens_per_call,
                min=1000,
            ).classes("w-full").bind_value(config, "estimated_tokens_per_call")

            ui.separator().classes("nav-separator")

            # ---- Retry Settings ----
            ui.label("Retry Settings").classes("section-header")
            ui.number(
                label="Max Retries",
                value=config.max_retries,
                min=0,
            ).classes("w-full").bind_value(config, "max_retries")
            ui.number(
                label="Retry Delay (s)",
                value=config.retry_delay,
                min=0.0,
                step=0.5,
                format="%.1f",
            ).classes("w-full").bind_value(config, "retry_delay")
            ui.number(
                label="Max Rate Limit Retries",
                value=config.max_rate_limit_retries,
                min=0,
            ).classes("w-full").bind_value(config, "max_rate_limit_retries")
            ui.number(
                label="Rate Limit Retry Delay (s)",
                value=config.rate_limit_retry_delay,
                min=0.0,
                step=0.5,
                format="%.1f",
            ).classes("w-full").bind_value(config, "rate_limit_retry_delay")

            ui.separator().classes("nav-separator")

            # ---- Environment ----
            ui.label("Environment").classes("section-header")
            ui.input(
                label="Python Path",
                value=config.python_path,
            ).classes("w-full").bind_value(config, "python_path")
            ui.input(
                label="Project Root",
                value=config.project_root,
            ).classes("w-full").bind_value(config, "project_root")
            ui.input(
                label="Progress File (optional)",
                value=config.progress_file,
            ).classes("w-full").bind_value(config, "progress_file")

            ui.separator().classes("nav-separator")

            # ---- Database (read-only from .env) ----
            ui.label("Database (from .env)").classes("section-header")
            ui.input(
                label="Host",
                value=secrets.db_host,
            ).classes("w-full").props("readonly")
            with ui.row().classes("w-full gap-sm"):
                ui.input(
                    label="Port",
                    value=secrets.db_port,
                ).classes("flex-1").props("readonly")
                ui.input(
                    label="Name",
                    value=secrets.db_name,
                ).classes("flex-1").props("readonly")
            ui.input(
                label="User",
                value=secrets.db_user,
            ).classes("w-full").props("readonly")
            ui.input(
                label="Password",
                value="••••••" if secrets.db_password else "",
            ).classes("w-full").props("readonly type=password")

            ui.separator().classes("nav-separator")

            ui.button(
                "Save Settings",
                on_click=lambda: _save_settings(),
                icon="save",
                color="primary",
            ).classes("w-full theme-btn-primary")

            def _save_settings() -> None:
                save_config(config)
                ui.notify("Settings saved", color="positive")

        with splitter.after, ui.card().classes("w-full q-pa-md theme-card"):
            ui.label("Execution").classes("section-header q-mb-sm")

            with ui.card().classes("w-full q-pa-sm q-mb-sm theme-card-elevated") as sc:
                stage_container.append(sc)
                create_stage_tracker(PIPELINE_STAGES, stage_statuses)

            ui.button(
                "Refresh",
                on_click=_refresh_stage_tracker,
                icon="refresh",
            ).props("outline size=sm").classes("q-mb-sm")

            status_lbl = ui.label("").classes("text-muted q-mb-sm")
            run_status_label.append(status_lbl)

            log_viewer = LogViewer()
            log_viewer_ref.append(log_viewer)

            async def _run_pipeline() -> None:
                # Disable before any guard check so rapid double-clicks can't
                # both pass an is_running check that is only true after the
                # first click acquires the lock.
                if run_btn_ref:
                    run_btn_ref[0].disable()

                try:
                    if lock.is_running:
                        ui.notify(
                            "A process is already running",
                            color="warning",
                        )
                        return

                    for s in PIPELINE_STAGES:
                        stage_statuses[s] = "pending"
                    if log_viewer_ref:
                        log_viewer_ref[0].clear()
                    if run_status_label:
                        run_status_label[0].text = "Running..."
                    _refresh_stage_tracker()

                    current_stage: list[str] = []

                    def _on_stdout(line: str) -> None:
                        if log_viewer_ref:
                            log_viewer_ref[0].append(line)

                    def _on_stderr(line: str) -> None:
                        if log_viewer_ref:
                            log_viewer_ref[0].append_stderr(line)

                    def _on_stage(stage: str) -> None:
                        if current_stage:
                            stage_statuses[current_stage[0]] = "completed"
                        current_stage.clear()
                        current_stage.append(stage)
                        stage_statuses[stage] = "running"
                        _refresh_stage_tracker()

                    started_at = datetime.now()
                    fresh_secrets = load_env_secrets(config.project_root)

                    missing: list[str] = []
                    if not fresh_secrets.anthropic_api_key:
                        missing.append("ANTHROPIC_API_KEY")
                    if not fresh_secrets.db_host:
                        missing.append("DB_HOST")
                    if missing:
                        ui.notify(
                            f"Missing credentials: {', '.join(missing)}",
                            color="warning",
                        )
                        return

                    try:
                        result = await runner.run(
                            config=config,
                            secrets=fresh_secrets,
                            on_stdout=_on_stdout,
                            on_stderr=_on_stderr,
                            on_stage=_on_stage,
                        )
                    except ValueError as exc:
                        ui.notify(str(exc), color="negative")
                        if run_status_label:
                            run_status_label[0].text = f"Failed: {exc}"
                        return
                    except Exception as exc:
                        ui.notify(f"Unexpected error: {exc}", color="negative")
                        if run_status_label:
                            run_status_label[0].text = f"Error: {exc}"
                        return

                    if current_stage:
                        stage_statuses[current_stage[0]] = (
                            "completed" if result.exit_code == 0 else "failed"
                        )
                    _refresh_stage_tracker()

                    status = "success" if result.exit_code == 0 else "failed"
                    if run_status_label:
                        run_status_label[
                            0
                        ].text = f"Finished: {status} (exit {result.exit_code})"

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
                    add_run_to_history(run_record)
                    ui.notify(
                        f"Run {status}",
                        color=("positive" if status == "success" else "negative"),
                    )
                finally:
                    if run_btn_ref:
                        run_btn_ref[0].enable()

            run_btn = ui.button(
                "Run Pipeline",
                on_click=_run_pipeline,
                icon="play_arrow",
                color="positive",
            ).classes("w-full q-mt-md theme-btn-primary")
            run_btn_ref.append(run_btn)
