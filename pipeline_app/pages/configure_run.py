"""Configure & Run page — config form and live execution panel."""

from __future__ import annotations

import dataclasses
import os
import shutil
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from nicegui import context, ui

from pipeline_app.components.confirm_dialog import confirm
from pipeline_app.components.log_viewer import (
    STDERR_CSS_CLASS,
    LogViewer,
    css_class_for,
    detect_severity,
)
from pipeline_app.components.path_picker import pick_path
from pipeline_app.components.preset_dialog import prompt_preset_name
from pipeline_app.components.stage_tracker import StageTracker, create_stage_tracker
from pipeline_app.config import (
    LLM_EFFORTS,
    LLM_MODELS,
    PROMPT_VERSIONS,
    PROVIDER_LABELS,
    EnvSecrets,
    PipelineAppConfig,
    add_run_to_history,
    delete_preset,
    load_env_secrets,
    load_preset,
    load_presets,
    save_config,
    strip_secrets_from_config,
    upsert_preset,
)
from pipeline_app.runner import (
    PIPELINE_STAGES,
    PipelineRunner,
    SubprocessLock,
    get_project_anchor,
)


def _python_fallback_dir(current: str) -> Path | None:
    """Resolve a bare name like 'python3' to its parent directory via PATH."""
    if not current or os.sep in current:
        return None
    resolved = shutil.which(current)
    return Path(resolved).parent if resolved else None


RUN_MODES = {
    "standard": "Standard",
    "local_pdfs": "Local PDFs",
    "pmid_list": "PMID List",
}


def create_configure_run_page(
    lock: SubprocessLock,
    config: PipelineAppConfig,
    secrets: EnvSecrets,
    runner: PipelineRunner,
) -> None:
    """Render the Configure & Run page."""
    ui.label("Configure & Run").classes("page-title")

    run_status_label: list[ui.label] = []
    stage_tracker_ref: list[StageTracker] = []
    log_viewer_ref: list[LogViewer] = []
    run_btn_ref: list[ui.button] = []

    def _refresh_stage_tracker() -> None:
        # In-place update: tracker elements already exist in the DOM, just
        # re-read runner.stage_statuses. No clear+rebuild, no visible flash.
        with suppress(RuntimeError):
            if stage_tracker_ref:
                stage_tracker_ref[0].update(runner.stage_statuses)

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
                ).props("flat").classes("btn-ghost")
                ui.button(
                    "Save",
                    on_click=lambda: _save_current_preset(),
                    icon="save",
                ).props("flat").classes("btn-ghost")
                ui.button(
                    "Delete",
                    on_click=lambda: _delete_preset(preset_select.value),
                    icon="delete",
                ).props("unelevated").classes("btn-destructive")

            async def _load_preset(preset_id: str | None) -> None:
                if not preset_id:
                    ui.notify("No preset selected", color="warning")
                    return
                # Loading overwrites every field in the live config — confirm
                # so a misclick doesn't silently discard unsaved form edits.
                confirmed = await confirm(
                    "Loading this preset will overwrite the current form "
                    "settings. Continue?",
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
                result = await prompt_preset_name()
                if not result:
                    return
                existing_names = {p.name for p in load_presets()}
                if result in existing_names:
                    confirmed = await confirm(
                        f"A preset named '{result}' already exists. Overwrite it?",
                        title="Overwrite Preset",
                    )
                    if not confirmed:
                        return
                updated = upsert_preset(result, config)
                ui.notify(f"Saved preset: {result}", color="positive")
                preset_select.options = {p.id: p.name for p in updated}
                # Name-match (not "last") so the right id is selected whether
                # we inserted or replaced.
                preset_select.value = next(
                    (p.id for p in updated if p.name == result), None
                )
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
                    # precision=0 blocks decimals; without it 0.5 slips past
                    # min=1 because int(0.5) == 0.
                    precision=0,
                ).classes("w-full").bind_value(config, "days_back")
                ui.checkbox("Dry Run").bind_value(config, "dry_run")
                ui.checkbox("Test Mode").bind_value(config, "test_mode")
                ui.checkbox("Sync External Data").bind_value(
                    config, "sync_external_data"
                )

            with (
                ui.column().classes("w-full") as local_pdfs_fields,
                ui.row().classes("w-full items-center gap-xs no-wrap"),
            ):
                local_pdfs_inp = (
                    ui.input(
                        label="Local PDFs Path",
                        value=config.local_pdfs_path,
                    )
                    .classes("flex-1")
                    .bind_value(config, "local_pdfs_path")
                )
                ui.button(
                    icon="folder_open",
                    on_click=lambda: _pick_local_pdfs(local_pdfs_inp),
                ).props("flat dense").classes("btn-icon")

            with (
                ui.column().classes("w-full") as pmid_list_fields,
                ui.row().classes("w-full items-center gap-xs no-wrap"),
            ):
                pmids_inp = (
                    ui.input(
                        label="PMIDs File Path",
                        value=config.pmids_path,
                    )
                    .classes("flex-1")
                    .bind_value(config, "pmids_path")
                )
                ui.button(
                    icon="folder_open",
                    on_click=lambda: _pick_pmids(pmids_inp),
                ).props("flat dense").classes("btn-icon")

            with ui.column().classes("w-full") as skip_validation_fields:
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

            async def _pick_pmids(inp: ui.input) -> None:
                anchor = get_project_anchor(config)
                if anchor is None:
                    ui.notify("Set Project Root first", color="warning")
                    return
                result = await pick_path(
                    mode="file",
                    anchor=anchor,
                    current_value=inp.value or "",
                    extensions=frozenset({".txt"}),
                    title="Select PMIDs file",
                )
                if result is not None:
                    inp.value = result

            def _update_run_mode_fields() -> None:
                mode = config.run_mode
                standard_fields.set_visibility(mode == "standard")
                local_pdfs_fields.set_visibility(mode == "local_pdfs")
                pmid_list_fields.set_visibility(mode == "pmid_list")
                skip_validation_fields.set_visibility(
                    mode in ("local_pdfs", "pmid_list")
                )

            run_mode_select.on_value_change(lambda _: _update_run_mode_fields())
            _update_run_mode_fields()

            ui.separator().classes("nav-separator")

            # ---- LLM Settings ----
            ui.label("LLM Settings").classes("section-header")

            provider_select = ui.select(
                options=PROVIDER_LABELS,
                label="Provider",
                value=config.llm_provider,
            ).classes("w-full").bind_value(config, "llm_provider")

            claude_model_select = ui.select(
                options=LLM_MODELS,
                label="Model",
                value=config.llm_model,
            ).classes("w-full").bind_value(config, "llm_model")
            claude_effort_select = ui.select(
                options=LLM_EFFORTS,
                label="Effort",
                value=config.llm_effort,
            ).classes("w-full").bind_value(config, "llm_effort")
            claude_max_tokens = ui.number(
                label="Max Tokens (0 = default)",
                value=config.llm_max_tokens,
                min=0,
            ).classes("w-full").bind_value(config, "llm_max_tokens")

            prompt_select = ui.select(
                options=PROMPT_VERSIONS,
                label="Prompt Version",
                value=config.prompt_version,
            ).classes("w-full").bind_value(config, "prompt_version")

            with ui.column().classes("w-full") as ollama_section:
                ollama_model_select = ui.select(
                    options=[config.ollama_model] if config.ollama_model else [],
                    label="Ollama model",
                    value=config.ollama_model,
                    with_input=True,
                    new_value_mode="add-unique",
                ).classes("w-full").bind_value(config, "ollama_model")
                ui.number(
                    label="Context window (num_ctx)",
                    value=config.ollama_num_ctx,
                    min=1024,
                    max=131_072,
                    step=1024,
                ).classes("w-full").bind_value(config, "ollama_num_ctx")

            async def _refresh_provider_ui() -> None:
                from pipeline_app.runner import list_ollama_models

                is_ollama = config.llm_provider == "ollama"
                claude_model_select.set_enabled(not is_ollama)
                claude_effort_select.set_enabled(not is_ollama)
                claude_max_tokens.set_enabled(not is_ollama)
                ollama_section.set_visibility(is_ollama)

                # Swap between the two canonical per-provider defaults; other
                # user-picked values are left alone.
                if is_ollama and config.prompt_version == "v5":
                    config.prompt_version = "ollama_v1"
                    prompt_select.set_value("ollama_v1")
                elif not is_ollama and config.prompt_version == "ollama_v1":
                    config.prompt_version = "v5"
                    prompt_select.set_value("v5")

                if not is_ollama:
                    return

                tags = await list_ollama_models(config.ollama_host)
                if tags:
                    current = (
                        config.ollama_model
                        if config.ollama_model in tags
                        else tags[0]
                    )
                    ollama_model_select.set_options(tags, value=current)
                    config.ollama_model = current

            provider_select.on_value_change(lambda _: _refresh_provider_ui())
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

            ui.timer(0, _refresh_provider_ui, once=True)

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
            with ui.row().classes("w-full items-center gap-xs no-wrap"):
                python_inp = (
                    ui.input(
                        label="Python Path",
                        value=config.python_path,
                    )
                    .classes("flex-1")
                    .bind_value(config, "python_path")
                )
                ui.button(
                    icon="folder_open",
                    on_click=lambda: _pick_python_path(python_inp),
                ).props("flat dense").classes("btn-icon")
            with ui.row().classes("w-full items-center gap-xs no-wrap"):
                project_inp = (
                    ui.input(
                        label="Project Root",
                        value=config.project_root,
                    )
                    .classes("flex-1")
                    .bind_value(config, "project_root")
                )
                ui.button(
                    icon="folder_open",
                    on_click=lambda: _pick_project_root(project_inp),
                ).props("flat dense").classes("btn-icon")
            with ui.row().classes("w-full items-center gap-xs no-wrap"):
                progress_inp = (
                    ui.input(
                        label="Progress File (optional)",
                        value=config.progress_file,
                    )
                    .classes("flex-1")
                    .bind_value(config, "progress_file")
                )
                ui.button(
                    icon="folder_open",
                    on_click=lambda: _pick_progress_file(progress_inp),
                ).props("flat dense").classes("btn-icon")

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
            ).props("unelevated").classes("w-full btn-primary")

            def _save_settings() -> None:
                save_config(config)
                ui.notify("Settings saved", color="positive")

        with splitter.after, ui.card().classes("w-full q-pa-md theme-card"):
            ui.label("Execution").classes("section-header q-mb-sm")

            with ui.card().classes("w-full q-pa-sm q-mb-sm theme-card-elevated"):
                stage_tracker_ref.append(
                    create_stage_tracker(PIPELINE_STAGES, runner.stage_statuses)
                )

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

            log_viewer = LogViewer()
            log_viewer_ref.append(log_viewer)

            def _on_stdout(line: str) -> None:
                if log_viewer_ref:
                    log_viewer_ref[0].append(line)

            def _on_stderr(line: str) -> None:
                if log_viewer_ref:
                    log_viewer_ref[0].append_stderr(line)

            def _on_stage(_stage: str) -> None:
                # Runner mutates stage_statuses before this callback fires;
                # the page just has to paint.
                _refresh_stage_tracker()

            # add_listener (instead of set_callbacks) so a second browser tab
            # opening mid-run doesn't overwrite the first tab's closures and
            # freeze its log pane. Each client registers its own bundle and
            # cleans up on disconnect.
            dispose = runner.add_listener(
                on_stdout=_on_stdout,
                on_stderr=_on_stderr,
                on_stage=_on_stage,
            )
            # context.client raises when invoked outside a live request
            # context (e.g. headless tests); suppress only the runtime
            # lookup, not an ImportError.
            with suppress(RuntimeError, AttributeError):
                context.client.on_disconnect(dispose)

            # Reconnect path: page may be rendering mid-run (or after the run
            # already finished). Replay buffered lines via load_batch so the
            # DOM paints in one cycle instead of N × 50 ms flush ticks, then
            # re-sync the tracker from runner.stage_statuses.
            if runner.log_lines:
                replay: list[tuple[str, str]] = []
                for type_, line in runner.log_lines:
                    if type_ == "out":
                        replay.append((line, css_class_for(detect_severity(line))))
                    else:
                        replay.append((f"[stderr] {line}", STDERR_CSS_CLASS))
                log_viewer.load_batch(replay)
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

                # Reset runner state before anything else so a validation-fail
                # retry doesn't keep showing the prior run's stage statuses.
                runner.reset_state()
                _refresh_stage_tracker()

                try:
                    if log_viewer_ref:
                        log_viewer_ref[0].clear()

                    started_at = datetime.now(UTC)
                    fresh_secrets = load_env_secrets(
                        config.project_root, use_cache=False
                    )

                    missing: list[str] = []
                    # Anthropic key only required when using the Anthropic
                    # provider; Ollama runs locally and has no API key.
                    if (
                        config.llm_provider == "anthropic"
                        and not fresh_secrets.anthropic_api_key
                    ):
                        missing.append("ANTHROPIC_API_KEY")
                    # DB_HOST not needed in dry-run mode (no database writes).
                    if not config.dry_run and not fresh_secrets.db_host:
                        missing.append("DB_HOST")
                    if missing:
                        ui.notify(
                            f"Missing credentials: {', '.join(missing)}",
                            color="warning",
                        )
                        return

                    # Status set after credential check so an early return
                    # doesn't leave "Running..." displayed.
                    _set_run_status("Running...", "neutral")

                    try:
                        result = await runner.run(
                            config=config,
                            secrets=fresh_secrets,
                        )
                    except ValueError as exc:
                        with suppress(RuntimeError):
                            ui.notify(str(exc), color="negative")
                        _set_run_status(f"Failed: {exc}", "negative")
                        _refresh_stage_tracker()
                        return
                    except RuntimeError as exc:
                        # "already running" from the lock's race-close check
                        # — keep separate from the catch-all so the notify
                        # color stays warning rather than negative.
                        with suppress(RuntimeError):
                            ui.notify(str(exc), color="warning")
                        _set_run_status(str(exc), "negative")
                        _refresh_stage_tracker()
                        return
                    except Exception as exc:
                        with suppress(RuntimeError):
                            ui.notify(f"Unexpected error: {exc}", color="negative")
                        _set_run_status(f"Error: {exc}", "negative")
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
                    add_run_to_history(run_record)
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
