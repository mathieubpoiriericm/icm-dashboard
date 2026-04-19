"""Tuning Config & Run page — form and stage execution panel."""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path

from nicegui import ui

from pipeline_app.components.log_viewer import LogViewer
from pipeline_app.components.path_picker import pick_path
from pipeline_app.components.stage_tracker import create_stage_tracker
from pipeline_app.config import (
    LLM_EFFORTS,
    LLM_MODELS,
    PROMPT_VERSIONS,
    PipelineAppConfig,
    TuningConfig,
    load_env_secrets,
    save_tuning_config,
)
from pipeline_app.runner import (
    TUNING_STAGES,
    SubprocessLock,
    TuningRunner,
    get_project_anchor,
)


def create_tuning_page(
    lock: SubprocessLock,
    config: PipelineAppConfig,
    tuning: TuningConfig,
    runner: TuningRunner,
) -> None:
    """Render the Tuning Config & Run page."""
    ui.label("Tuning").classes("page-title")

    stage_container: list[ui.element] = []
    log_viewer_ref: list[LogViewer] = []
    output_links_container: list[ui.element] = []

    def _refresh_stage_tracker(
        repeat: int = 0,
        total: int = 0,
    ) -> None:
        if stage_container:
            stage_container[0].clear()
            with stage_container[0]:
                create_stage_tracker(
                    TUNING_STAGES,
                    runner.stage_statuses,
                    repeat,
                    total,
                    stage_durations=runner.stage_durations,
                )

    with ui.splitter(value=40).classes("w-full") as splitter:
        with splitter.before, ui.card().classes("w-full q-pa-md theme-card"):
            ui.label("Tuning Configuration").classes("section-header q-mb-sm")

            with ui.row().classes("w-full items-center gap-xs no-wrap"):
                pdf_inp = (
                    ui.input(
                        label="PDF Path",
                        value=tuning.pdf_path,
                    )
                    .classes("flex-1")
                    .bind_value(tuning, "pdf_path")
                )
                ui.button(
                    icon="folder_open",
                    on_click=lambda: _pick_pdf_path(pdf_inp),
                ).props("flat dense").classes("theme-btn-ghost")

            with ui.row().classes("w-full items-center gap-xs no-wrap"):
                gold_inp = (
                    ui.input(
                        label="Gold Standard Path",
                        value=tuning.gold_standard_path,
                    )
                    .classes("flex-1")
                    .bind_value(tuning, "gold_standard_path")
                )
                ui.button(
                    icon="folder_open",
                    on_click=lambda: _pick_gold_standard(gold_inp),
                ).props("flat dense").classes("theme-btn-ghost")

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
                    title="Select PDF for tuning",
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

            ui.number(
                label="Confidence Threshold",
                value=tuning.confidence_threshold,
                min=0.0,
                max=1.0,
                step=0.01,
                format="%.2f",
            ).classes("w-full").bind_value(tuning, "confidence_threshold")

            ui.number(
                label="Repeats",
                value=tuning.repeats,
                min=1,
                max=20,
            ).classes("w-full").bind_value(tuning, "repeats")

            ui.checkbox("Auto-Advance Stages").bind_value(tuning, "auto_advance")

            ui.number(
                label="F-Beta Weight",
                value=tuning.f_beta_weight,
                min=0.5,
                max=5.0,
                step=0.1,
                format="%.1f",
            ).classes("w-full").bind_value(tuning, "f_beta_weight")
            with (
                ui.element("div")
                .classes("rounded-borders q-pa-sm q-mt-xs")
                .style(
                    "background: rgba(255,255,255,0.04);"
                    " border: 1px solid rgba(255,255,255,0.08);"
                )
            ):
                ui.label(
                    "Beta (β) is the weight parameter that controls how"
                    " much more recall matters relative to precision."
                    " F-beta is the score computed from that weight — the"
                    " weighted harmonic mean of precision and recall:"
                    " F_β = (1+β²)·P·R / (β²·P+R)."
                ).classes("text-caption text-grey")
                ui.label(
                    "β=1 (F1): precision and recall weighted equally."
                    " β=2 (F2, default): recall weighted 2× more than"
                    " precision. β=0.5 (F0.5): precision weighted 2×"
                    " more than recall."
                ).classes("text-caption text-grey q-mt-xs")
                ui.label(
                    "The default β=2 favors gene discovery because"
                    " missing a real causal gene (low recall) is harder"
                    " to catch than including a spurious one (low"
                    " precision), which downstream review can filter out."
                ).classes("text-caption text-grey q-mt-xs")

            ui.textarea(
                label="Notes",
                value=tuning.notes,
            ).classes("w-full").bind_value(tuning, "notes")

            ui.separator().classes("nav-separator")
            ui.label("LLM Settings").classes("section-header")

            ui.checkbox("Use Main Config LLM Settings").bind_value(
                tuning, "use_main_config"
            )

            with ui.column().classes("w-full") as llm_override_fields:
                ui.select(
                    options=LLM_MODELS,
                    label="Model",
                    value=tuning.llm_model,
                ).classes("w-full").bind_value(tuning, "llm_model")
                ui.select(
                    options=LLM_EFFORTS,
                    label="Effort",
                    value=tuning.llm_effort,
                ).classes("w-full").bind_value(tuning, "llm_effort")
                ui.number(
                    label="Max Tokens (0 = default)",
                    value=tuning.llm_max_tokens,
                    min=0,
                ).classes("w-full").bind_value(tuning, "llm_max_tokens")
                ui.select(
                    options=PROMPT_VERSIONS,
                    label="Prompt Version",
                    value=tuning.prompt_version,
                ).classes("w-full").bind_value(tuning, "prompt_version")

            llm_override_fields.bind_visibility_from(
                tuning, "use_main_config", backward=lambda v: not v
            )

            ui.separator().classes("nav-separator")
            ui.button(
                "Save Settings",
                on_click=lambda: _save_tuning_settings(),
                icon="save",
                color="primary",
            ).classes("w-full theme-btn-primary")

            def _save_tuning_settings() -> None:
                save_tuning_config(tuning)
                ui.notify("Tuning settings saved", color="positive")

        with splitter.after, ui.card().classes("w-full q-pa-md theme-card"):
            ui.label("Execution").classes("section-header q-mb-sm")

            with ui.card().classes("w-full q-pa-sm q-mb-sm theme-card-elevated") as sc:
                stage_container.append(sc)
                create_stage_tracker(
                    TUNING_STAGES,
                    runner.stage_statuses,
                    runner.current_repeat,
                    runner.total_repeats,
                    stage_durations=runner.stage_durations,
                )

            ui.button(
                "Refresh",
                on_click=lambda: _refresh_stage_tracker(),
                icon="refresh",
            ).props("outline").classes("q-mb-sm")

            with ui.column().classes("w-full q-mb-sm") as olc:
                output_links_container.append(olc)

            log_viewer = LogViewer()
            log_viewer_ref.append(log_viewer)

            run_btn = ui.button(
                "Run",
                icon="play_arrow",
                color="positive",
            ).classes("w-full q-mt-md theme-btn-primary")
            with ui.row().classes("q-mt-sm gap-sm"):
                next_btn = ui.button(
                    "Next Stage",
                    on_click=lambda: runner.advance(),
                    icon="skip_next",
                    color="primary",
                ).props("outline")
                next_btn.set_visibility(False)
                skip_btn = (
                    ui.button(
                        "Skip Stage",
                        on_click=lambda: runner.skip(),
                        icon="fast_forward",
                        color="warning",
                    )
                    .props("flat")
                    .classes("theme-btn-ghost")
                )
                skip_btn.set_visibility(False)

            # UI callbacks — update elements only (state buffered by runner)
            def _on_stdout(line: str) -> None:
                with suppress(RuntimeError):
                    if log_viewer_ref:
                        log_viewer_ref[0].append(line)

            def _on_stderr(line: str) -> None:
                with suppress(RuntimeError):
                    if log_viewer_ref:
                        log_viewer_ref[0].append_stderr(line)

            def _on_stage_start(
                _stage: str,
                repeat: int,
                total: int,
            ) -> None:
                with suppress(RuntimeError):
                    next_btn.set_visibility(False)
                    skip_btn.set_visibility(False)
                    _refresh_stage_tracker(repeat, total)

            def _on_stage_complete(
                stage: str,
                output_files: list[Path],
            ) -> None:
                with suppress(RuntimeError):
                    # Pass the runner's live repeat counters so the tracker
                    # keeps showing "Repeat X/Y" between stages of a
                    # multi-repeat run.
                    _refresh_stage_tracker(
                        runner.current_repeat,
                        runner.total_repeats,
                    )
                    if output_files and output_links_container:
                        with output_links_container[0]:
                            for f in output_files:
                                # Stage prefix disambiguates outputs across
                                # multiple stages within the same run.
                                ui.label(f"[{stage}] {f.name}").classes(
                                    "text-muted"
                                ).style("color: var(--theme-secondary)")

            def _on_waiting() -> None:
                with suppress(RuntimeError):
                    next_btn.set_visibility(True)
                    skip_btn.set_visibility(True)

            runner.set_callbacks(
                on_stdout=_on_stdout,
                on_stderr=_on_stderr,
                on_stage_start=_on_stage_start,
                on_stage_complete=_on_stage_complete,
                on_waiting=_on_waiting,
            )

            # Restore buffered state (previous or in-progress run)
            if runner.log_lines:
                for type_, line in runner.log_lines:
                    if type_ == "out":
                        log_viewer_ref[0].append(line)
                    else:
                        log_viewer_ref[0].append_stderr(line)
                _refresh_stage_tracker(
                    runner.current_repeat,
                    runner.total_repeats,
                )
            if lock.is_running:
                run_btn.disable()
            if runner.is_waiting:
                # Reconnecting mid-wait: the runner is paused awaiting user
                # input but won't re-emit _on_waiting. Show the controls now
                # so the user isn't stranded with hidden Next/Skip buttons.
                next_btn.set_visibility(True)
                skip_btn.set_visibility(True)

            async def _run_tuning() -> None:
                # any_running covers inter-stage waits when the lock is
                # momentarily released but the experiment is still live —
                # without this check, a second click would call
                # runner.reset_state() mid-run and corrupt the stage tracker.
                if runner.any_running:
                    ui.notify("A process is already running", color="warning")
                    return

                if log_viewer_ref:
                    log_viewer_ref[0].clear()
                if output_links_container:
                    output_links_container[0].clear()
                # Reset before refresh so the tracker doesn't flash the
                # previous run's terminal statuses.
                runner.reset_state()
                _refresh_stage_tracker()

                run_btn.disable()
                try:
                    fresh_secrets = load_env_secrets(config.project_root)
                    await runner.run_experiment(
                        config=config,
                        tuning=tuning,
                        secrets=fresh_secrets,
                    )
                    with suppress(RuntimeError):
                        if runner.was_cancelled:
                            ui.notify("Tuning experiment cancelled", color="warning")
                        else:
                            ui.notify("Tuning experiment complete", color="positive")
                except Exception as e:
                    with suppress(RuntimeError):
                        ui.notify(f"Error: {e}", color="negative")
                finally:
                    # Detached-client cleanup: a user who navigates away leaves
                    # the captured elements attached to a discarded client and
                    # NiceGUI raises RuntimeError on enable/set_visibility.
                    with suppress(RuntimeError):
                        _refresh_stage_tracker()
                    with suppress(RuntimeError):
                        run_btn.enable()
                    with suppress(RuntimeError):
                        next_btn.set_visibility(False)
                    with suppress(RuntimeError):
                        skip_btn.set_visibility(False)

            run_btn.on_click(_run_tuning)
