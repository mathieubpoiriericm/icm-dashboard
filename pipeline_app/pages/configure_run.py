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

from pipeline.config import OLLAMA_PROMPT_VERSIONS
from pipeline_app.components.execution_panel import ExecutionPanel
from pipeline_app.components.form_fields import (
    bound_number,
    bound_path_input,
    bound_select,
)
from pipeline_app.components.path_picker import pick_path
from pipeline_app.components.preset_selector import create_preset_selector
from pipeline_app.components.provider_section import apply_provider_widget_state
from pipeline_app.config import (
    LLM_EFFORTS,
    LLM_MODELS,
    PROMPT_VERSIONS,
    PROVIDER_LABELS,
    EnvSecrets,
    PipelineAppConfig,
    add_run_to_history,
    load_env_secrets,
    save_config,
    strip_secrets_from_config,
)
from pipeline_app.runner import (
    PIPELINE_STAGES,
    PipelineRunner,
    SubprocessLock,
    get_project_anchor,
    list_ollama_models,
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
    panel_ref: list[ExecutionPanel] = []
    run_btn_ref: list[ui.button] = []

    def _refresh_stage_tracker() -> None:
        if panel_ref:
            panel_ref[0].refresh(runner.stage_statuses)

    with ui.splitter(value=40).classes("w-full") as splitter:
        with splitter.before, ui.card().classes("w-full q-pa-md theme-card"):
            # ---- Presets ----
            create_preset_selector(config)

            ui.separator().classes("nav-separator")

            # ---- Run Mode ----
            ui.label("Run Mode").classes("section-header")
            bound_select(config, "run_mode", options=RUN_MODES, label="Mode")

            with ui.column().classes("w-full") as standard_fields:
                bound_number(
                    config,
                    "days_back",
                    label="Days Back",
                    min=1,
                    max=365,
                    # precision=0 blocks decimals; without it 0.5 slips past
                    # min=1 because int(0.5) == 0.
                    precision=0,
                )
                ui.checkbox("Dry Run").bind_value(config, "dry_run")
                ui.checkbox("Test Mode").bind_value(config, "test_mode")
                ui.checkbox("Sync External Data").bind_value(
                    config, "sync_external_data"
                )

            with ui.column().classes("w-full") as local_pdfs_fields:
                bound_path_input(
                    config,
                    "local_pdfs_path",
                    label="Local PDFs Path",
                    on_pick=lambda inp: _pick_local_pdfs(inp),
                )

            with ui.column().classes("w-full") as pmid_list_fields:
                bound_path_input(
                    config,
                    "pmids_path",
                    label="PMIDs File Path",
                    on_pick=lambda inp: _pick_pmids(inp),
                )

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

            # Declarative bindings so programmatic changes to config.run_mode
            # (e.g. preset load) refresh visibility too — an on_value_change
            # listener only fires on UI-originated changes.
            standard_fields.bind_visibility_from(
                config, "run_mode", backward=lambda v: v == "standard"
            )
            local_pdfs_fields.bind_visibility_from(
                config, "run_mode", backward=lambda v: v == "local_pdfs"
            )
            pmid_list_fields.bind_visibility_from(
                config, "run_mode", backward=lambda v: v == "pmid_list"
            )
            skip_validation_fields.bind_visibility_from(
                config,
                "run_mode",
                backward=lambda v: v in ("local_pdfs", "pmid_list"),
            )

            ui.separator().classes("nav-separator")

            # ---- LLM Settings ----
            ui.label("LLM Settings").classes("section-header")

            provider_select = (
                bound_select(
                    config,
                    "llm_provider",
                    options=PROVIDER_LABELS,
                    label="Provider",
                )
            )

            claude_model_select = (
                bound_select(config, "llm_model", options=LLM_MODELS, label="Model")
            )
            claude_effort_select = (
                bound_select(
                    config,
                    "llm_effort",
                    options=LLM_EFFORTS,
                    label="Effort",
                )
            )
            claude_max_tokens = (
                bound_number(
                    config,
                    "llm_max_tokens",
                    label="Max Tokens (0 = default)",
                    min=0,
                )
            )

            prompt_select = (
                bound_select(
                    config,
                    "prompt_version",
                    options=PROMPT_VERSIONS,
                    label="Prompt Version",
                )
            )

            with ui.column().classes("w-full") as ollama_section:
                ollama_model_select = (
                    ui.select(
                        options=[config.ollama_model] if config.ollama_model else [],
                        label="Ollama model",
                        value=config.ollama_model,
                        with_input=True,
                        new_value_mode="add-unique",
                    )
                    .classes("w-full")
                    .bind_value(config, "ollama_model")
                )
                bound_number(
                    config,
                    "ollama_num_ctx",
                    label="Context window (num_ctx)",
                    min=1024,
                    max=131_072,
                    step=1024,
                )

            async def _refresh_provider_ui() -> None:
                is_ollama = apply_provider_widget_state(
                    config.llm_provider,
                    claude_widgets=(
                        claude_model_select,
                        claude_effort_select,
                        claude_max_tokens,
                    ),
                )
                ollama_section.set_visibility(is_ollama)

                # Swap between canonical per-provider defaults; user-picked
                # values in the same family (e.g. an explicit "v4" on Anthropic
                # or a future "ollama_v2") are left alone.
                if is_ollama and config.prompt_version not in OLLAMA_PROMPT_VERSIONS:
                    config.prompt_version = "ollama_v1"
                    prompt_select.set_value("ollama_v1")
                elif not is_ollama and config.prompt_version in OLLAMA_PROMPT_VERSIONS:
                    config.prompt_version = "v5"
                    prompt_select.set_value("v5")

                if not is_ollama:
                    return

                tags = await list_ollama_models(config.ollama_host)
                if tags:
                    current = (
                        config.ollama_model if config.ollama_model in tags else tags[0]
                    )
                    ollama_model_select.set_options(tags, value=current)
                    config.ollama_model = current

            provider_select.on_value_change(lambda _: _refresh_provider_ui())
            bound_number(
                config,
                "confidence_threshold",
                label="Confidence Threshold",
                min=0.0,
                max=1.0,
                step=0.01,
                format="%.2f",
            )
            bound_number(
                config,
                "max_paper_text_chars",
                label="Max Paper Text Chars",
                min=1000,
            )

            ui.timer(0, _refresh_provider_ui, once=True)

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

            def _on_stdout(line: str) -> None:
                if panel_ref:
                    panel_ref[0].append_stdout(line)

            def _on_stderr(line: str) -> None:
                if panel_ref:
                    panel_ref[0].append_stderr(line)

            def _on_stage(_stage: str) -> None:
                # Runner mutates stage_statuses before this callback fires;
                # the page just has to paint.
                _refresh_stage_tracker()

            # Each client registers its own listener bundle and cleans up on
            # disconnect so concurrent tabs keep receiving updates.
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
                panel.replay(runner.log_lines)
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
                    if panel_ref:
                        panel_ref[0].clear_log()

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
                    except Exception as exc:
                        # RuntimeError here is "already running" from the
                        # lock's race-close check — warning color vs the
                        # negative for the validation/unknown cases.
                        if isinstance(exc, RuntimeError):
                            notify_color, notify_msg = "warning", str(exc)
                            status_text = str(exc)
                        elif isinstance(exc, ValueError):
                            notify_color, notify_msg = "negative", str(exc)
                            status_text = f"Failed: {exc}"
                        else:
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
