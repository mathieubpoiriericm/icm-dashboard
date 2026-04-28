"""Tests for tuning stage commands and orchestration."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pipeline_app.components.log_viewer import MAX_LOG_LINES
from pipeline_app.config import (
    EnvSecrets,
    PipelineAppConfig,
    TuningConfig,
)
from pipeline_app.runner import (
    RSCRIPT_EXE,
    TUNING_STAGES,
    SubprocessLock,
    TuningRunner,
    build_extract_config,
    build_tuning_stage_command,
    get_tuning_project_root,
)


class TestBuildTuningStageCommand:
    def test_extract_stage(self):
        tuning = TuningConfig(pdf_path="/data/pdfs")
        exe, args = build_tuning_stage_command("extract", tuning)
        assert exe == "python3"
        assert args == [
            "pipeline/main.py",
            "--local-pdfs",
            "/data/pdfs",
            "--skip-validation",
            "--dry-run",
        ]

    def test_validate_stage(self):
        tuning = TuningConfig(
            gold_standard_path="/data/gs.csv",
        )
        exe, args = build_tuning_stage_command(
            "validate",
            tuning,
            report_path="/logs/report.json",
        )
        assert exe == "python3"
        assert "/logs/report.json" in args
        assert "--reference" in args
        assert "--local-pdfs" in args

    def test_error_analysis_stage(self):
        tuning = TuningConfig(
            gold_standard_path="/data/gs.csv",
        )
        exe, args = build_tuning_stage_command(
            "error_analysis",
            tuning,
            report_path="/logs/report.json",
        )
        assert exe == "python3"
        assert "scripts/tuning/analyze_errors.py" in args
        assert "/logs/report.json" in args

    def test_calibrate_stage(self):
        tuning = TuningConfig(f_beta_weight=1.5)
        exe, args = build_tuning_stage_command(
            "calibrate",
            tuning,
            score_dist_path="/logs/scores.csv",
        )
        assert exe == "python3"
        assert "/logs/scores.csv" in args
        assert "--beta" in args
        assert "1.5" in args

    def test_track_stage(self):
        tuning = TuningConfig(
            gold_standard_path="/data/gs.csv",
            notes="test run",
        )
        exe, args = build_tuning_stage_command(
            "track",
            tuning,
            report_path="/logs/report.json",
            run_group="group-1",
        )
        assert "--pipeline-report" in args
        assert "--notes" in args
        assert "--run-group" in args
        assert "group-1" in args

    def test_track_stage_omits_empty_notes(self):
        tuning = TuningConfig(notes="")
        exe, args = build_tuning_stage_command(
            "track",
            tuning,
            report_path="/logs/report.json",
        )
        assert "--notes" not in args

    def test_track_stage_strips_newlines_from_notes(self):
        # Regression: ui.textarea allows multi-line notes. Without
        # sanitization the value would corrupt the tuning_runs.csv row.
        tuning = TuningConfig(notes="line one\nline two\rline three")
        _, args = build_tuning_stage_command(
            "track",
            tuning,
            report_path="/logs/report.json",
        )
        idx = args.index("--notes") + 1
        sanitized = args[idx]
        assert "\n" not in sanitized
        assert "\r" not in sanitized
        assert sanitized == "line one line two line three"

    def test_track_stage_preserves_notes_without_newlines(self):
        tuning = TuningConfig(notes="single-line note")
        _, args = build_tuning_stage_command(
            "track",
            tuning,
            report_path="/logs/report.json",
        )
        idx = args.index("--notes") + 1
        assert args[idx] == "single-line note"

    def test_plot_stage(self):
        tuning = TuningConfig()
        exe, args = build_tuning_stage_command("plot", tuning)
        assert exe == RSCRIPT_EXE
        assert args == ["scripts/plot_tuning_runs.R"]

    def test_validate_raises_without_report_path(self):
        tuning = TuningConfig(gold_standard_path="/data/gs.csv")
        with pytest.raises(ValueError, match="requires report_path"):
            build_tuning_stage_command("validate", tuning)

    def test_error_analysis_raises_without_report_path(self):
        tuning = TuningConfig(gold_standard_path="/data/gs.csv")
        with pytest.raises(ValueError, match="requires report_path"):
            build_tuning_stage_command("error_analysis", tuning)

    def test_calibrate_raises_without_score_dist_path(self):
        tuning = TuningConfig(f_beta_weight=1.5)
        with pytest.raises(ValueError, match="requires score_dist_path"):
            build_tuning_stage_command("calibrate", tuning)

    def test_track_raises_without_report_path(self):
        tuning = TuningConfig(gold_standard_path="/data/gs.csv")
        with pytest.raises(ValueError, match="requires report_path"):
            build_tuning_stage_command("track", tuning)

    def test_unknown_stage_raises(self):
        with pytest.raises(ValueError, match="Unknown stage"):
            build_tuning_stage_command("unknown", TuningConfig())


class TestBuildExtractConfig:
    def test_uses_main_config_when_flagged(self):
        main = PipelineAppConfig(
            python_path="/usr/bin/python3",
            project_root="/project",
            llm_model="claude-opus-4-6",
        )
        tuning = TuningConfig(
            use_main_config=True,
            pdf_path="/data/pdfs",
            confidence_threshold=0.7,
            llm_model="claude-sonnet-4-6",
        )
        result = build_extract_config(main, tuning)
        assert result.python_path == "/usr/bin/python3"
        assert result.project_root == "/project"
        assert result.llm_model == "claude-opus-4-6"
        assert result.run_mode == "local_pdfs"
        assert result.local_pdfs_path == "/data/pdfs"
        assert result.skip_validation is True
        assert result.dry_run is True
        assert result.confidence_threshold == 0.7

    def test_uses_tuning_overrides_when_not_main(self):
        main = PipelineAppConfig(llm_model="claude-opus-4-6")
        tuning = TuningConfig(
            use_main_config=False,
            python_path="/custom/python",
            project_root="/custom/root",
            llm_model="claude-sonnet-4-6",
            llm_effort="low",
            pdf_path="/data/pdfs",
        )
        result = build_extract_config(main, tuning)
        assert result.python_path == "/custom/python"
        assert result.project_root == "/custom/root"
        assert result.llm_model == "claude-sonnet-4-6"
        assert result.llm_effort == "low"


class TestGetTuningProjectRoot:
    def test_uses_main_root_when_flagged(self):
        main = PipelineAppConfig(project_root="/main")
        tuning = TuningConfig(use_main_config=True, project_root="/tuning")
        assert get_tuning_project_root(main, tuning) == "/main"

    def test_uses_tuning_root_when_overridden(self):
        main = PipelineAppConfig(project_root="/main")
        tuning = TuningConfig(use_main_config=False, project_root="/tuning")
        assert get_tuning_project_root(main, tuning) == "/tuning"

    def test_empty_tuning_root_falls_back_to_main(self):
        main = PipelineAppConfig(project_root="/main")
        tuning = TuningConfig(use_main_config=False, project_root="")
        assert get_tuning_project_root(main, tuning) == "/main"


class TestTuningRunner:
    @pytest.mark.asyncio
    async def test_runs_all_stages_with_auto_advance(
        self,
        project_dir: Path,
    ):
        stages_started: list[str] = []
        stages_completed: list[str] = []

        script = project_dir / "noop.py"
        script.write_text("print('done')\n")

        config = PipelineAppConfig(
            python_path="python3",
            project_root=str(project_dir),
        )
        tuning = TuningConfig(
            auto_advance=True,
            pdf_path=str(project_dir),
            gold_standard_path=str(project_dir / "gs.csv"),
        )
        secrets = EnvSecrets()
        lock = SubprocessLock()
        runner = TuningRunner(lock)

        # Create dummy output files so stage data passing works
        logs_json = project_dir / "logs" / "json"
        logs_json.mkdir(parents=True)
        report = logs_json / "pipeline_report_test.json"
        report.write_text("{}")
        logs_tuning = project_dir / "logs" / "tuning" / "score_distributions"
        logs_tuning.mkdir(parents=True)
        score_dist = logs_tuning / "score_distribution_test.csv"
        score_dist.write_text("gene,score\nA,0.5\n")

        runner.set_callbacks(
            on_stdout=lambda _: None,
            on_stderr=lambda _: None,
            on_stage_start=lambda s, r, t: stages_started.append(s),
            on_stage_complete=lambda s, _: stages_completed.append(s),
            on_waiting=lambda: None,
        )

        await runner.run_experiment(
            config=config,
            tuning=tuning,
            secrets=secrets,
            script_override=str(script),
        )

        assert stages_started == TUNING_STAGES
        assert stages_completed == TUNING_STAGES

    @pytest.mark.asyncio
    async def test_use_main_config_false_runs_from_tuning_project_root(
        self,
        project_dir: Path,
        tmp_path: Path,
    ):
        script = project_dir / "noop.py"
        script.write_text("print('done')\n")

        config = PipelineAppConfig(
            python_path="python3",
            project_root=str(tmp_path / "not-a-project"),
        )
        tuning = TuningConfig(
            auto_advance=True,
            use_main_config=False,
            project_root=str(project_dir),
            python_path="python3",
            pdf_path=str(project_dir),
            gold_standard_path=str(project_dir / "gs.csv"),
        )

        stages_started: list[str] = []
        runner = TuningRunner(SubprocessLock())
        runner.set_callbacks(
            on_stdout=lambda _: None,
            on_stderr=lambda _: None,
            on_stage_start=lambda s, *_: stages_started.append(s),
            on_stage_complete=lambda *_: None,
            on_waiting=lambda: None,
        )

        await runner.run_experiment(
            config=config,
            tuning=tuning,
            secrets=EnvSecrets(),
            script_override=str(script),
        )

        assert stages_started == TUNING_STAGES


class TestTuningRunnerBuffering:
    """log_lines is a deque(maxlen=MAX_LOG_LINES): once full, new appends
    evict the oldest entry (rotating window) instead of silently dropping
    the newest. This matches the UI's ui.log max_lines rotation so a
    reconnect replay and the live log show the same window.
    """

    def test_emit_stdout_caps_at_max_and_evicts_oldest(self):
        lock = SubprocessLock()
        runner = TuningRunner(lock)
        for i in range(MAX_LOG_LINES - 1):
            runner._emit_stdout(f"line_{i}")
        runner._emit_stdout("last allowed")
        assert len(runner.log_lines) == MAX_LOG_LINES
        runner._emit_stdout("over the cap")
        assert len(runner.log_lines) == MAX_LOG_LINES
        # Oldest evicted; newest retained.
        assert runner.log_lines[0] == ("out", "line_1")
        assert runner.log_lines[-1] == ("out", "over the cap")

    def test_emit_stderr_caps_at_max_and_evicts_oldest(self):
        lock = SubprocessLock()
        runner = TuningRunner(lock)
        for i in range(MAX_LOG_LINES - 1):
            runner._emit_stderr(f"err_{i}")
        runner._emit_stderr("last allowed")
        assert len(runner.log_lines) == MAX_LOG_LINES
        runner._emit_stderr("over the cap")
        assert len(runner.log_lines) == MAX_LOG_LINES
        assert runner.log_lines[-1] == ("err", "over the cap")

    def test_mixed_stdout_stderr_shares_cap(self):
        lock = SubprocessLock()
        runner = TuningRunner(lock)
        for i in range(MAX_LOG_LINES - 2):
            runner._emit_stdout(f"out_{i}")
        runner._emit_stdout("stdout")
        runner._emit_stderr("stderr")
        assert len(runner.log_lines) == MAX_LOG_LINES
        runner._emit_stdout("over cap")
        assert len(runner.log_lines) == MAX_LOG_LINES
        assert runner.log_lines[-1] == ("out", "over cap")

    def test_log_lines_tagged_correctly(self):
        lock = SubprocessLock()
        runner = TuningRunner(lock)
        runner._emit_stdout("stdout line")
        runner._emit_stderr("stderr line")
        assert runner.log_lines[0] == ("out", "stdout line")
        assert runner.log_lines[1] == ("err", "stderr line")


class TestTuningRunnerStageStatuses:
    def test_initial_statuses_all_pending(self):
        lock = SubprocessLock()
        runner = TuningRunner(lock)
        for stage in TUNING_STAGES:
            assert runner.stage_statuses[stage] == "pending"

    def test_emit_stage_start_sets_running(self):
        lock = SubprocessLock()
        runner = TuningRunner(lock)
        runner._emit_stage_start("extract", 1, 1)
        assert runner.stage_statuses["extract"] == "running"

    def test_emit_stage_complete_sets_completed(self):
        lock = SubprocessLock()
        runner = TuningRunner(lock)
        runner._emit_stage_start("extract", 1, 1)
        runner._emit_stage_complete("extract", [])
        assert runner.stage_statuses["extract"] == "completed"

    def test_emit_stage_start_tracks_repeat(self):
        lock = SubprocessLock()
        runner = TuningRunner(lock)
        runner._emit_stage_start("extract", 2, 3)
        assert runner.current_repeat == 2
        assert runner.total_repeats == 3


class TestTuningRunnerCallbacks:
    def test_set_callbacks_replaces_existing(self):
        lock = SubprocessLock()
        runner = TuningRunner(lock)
        first: list[str] = []
        runner.set_callbacks(
            on_stdout=first.append,
            on_stderr=lambda _: None,
            on_stage_start=lambda *_: None,
            on_stage_complete=lambda *_: None,
            on_waiting=lambda: None,
        )
        runner._emit_stdout("first")
        second: list[str] = []
        runner.set_callbacks(
            on_stdout=second.append,
            on_stderr=lambda _: None,
            on_stage_start=lambda *_: None,
            on_stage_complete=lambda *_: None,
            on_waiting=lambda: None,
        )
        runner._emit_stdout("second")
        assert first == ["first"]
        assert second == ["second"]

    def test_emit_stdout_calls_callback(self):
        lock = SubprocessLock()
        runner = TuningRunner(lock)
        lines: list[str] = []
        runner.set_callbacks(
            on_stdout=lines.append,
            on_stderr=lambda _: None,
            on_stage_start=lambda *_: None,
            on_stage_complete=lambda *_: None,
            on_waiting=lambda: None,
        )
        runner._emit_stdout("hello")
        assert lines == ["hello"]

    def test_emit_stderr_calls_callback(self):
        lock = SubprocessLock()
        runner = TuningRunner(lock)
        lines: list[str] = []
        runner.set_callbacks(
            on_stdout=lambda _: None,
            on_stderr=lines.append,
            on_stage_start=lambda *_: None,
            on_stage_complete=lambda *_: None,
            on_waiting=lambda: None,
        )
        runner._emit_stderr("error")
        assert lines == ["error"]

    def test_emit_without_callbacks_does_not_crash(self):
        lock = SubprocessLock()
        runner = TuningRunner(lock)
        runner._emit_stdout("line")
        runner._emit_stderr("error")
        runner._emit_stage_start("extract", 1, 1)
        runner._emit_stage_complete("extract", [])
        runner._emit_waiting()


class TestTuningRunnerCancel:
    @pytest.mark.asyncio
    async def test_cancel_stops_experiment_between_stages(
        self,
        project_dir: Path,
    ):
        script = project_dir / "noop.py"
        script.write_text("print('done')\n")

        config = PipelineAppConfig(
            python_path="python3",
            project_root=str(project_dir),
        )
        tuning = TuningConfig(
            auto_advance=False,
            pdf_path=str(project_dir),
            gold_standard_path=str(project_dir / "gs.csv"),
        )

        waiting_event = asyncio.Event()
        stages_completed: list[str] = []

        lock = SubprocessLock()
        runner = TuningRunner(lock)
        runner.set_callbacks(
            on_stdout=lambda _: None,
            on_stderr=lambda _: None,
            on_stage_start=lambda *_: None,
            on_stage_complete=lambda s, _: stages_completed.append(s),
            on_waiting=lambda: waiting_event.set(),
        )

        task = asyncio.create_task(
            runner.run_experiment(
                config=config,
                tuning=tuning,
                secrets=EnvSecrets(),
                script_override=str(script),
            )
        )

        await asyncio.wait_for(waiting_event.wait(), timeout=10)
        await runner.cancel()
        await task

        assert stages_completed == ["extract"]


class TestTuningRunnerAdvanceSkip:
    @pytest.mark.asyncio
    async def test_advance_proceeds_to_next_stage(self, project_dir: Path):
        script = project_dir / "noop.py"
        script.write_text("print('done')\n")

        config = PipelineAppConfig(
            python_path="python3",
            project_root=str(project_dir),
        )
        tuning = TuningConfig(
            auto_advance=False,
            pdf_path=str(project_dir),
            gold_standard_path=str(project_dir / "gs.csv"),
        )

        waiting_event = asyncio.Event()
        stages_started: list[str] = []

        lock = SubprocessLock()
        runner = TuningRunner(lock)
        runner.set_callbacks(
            on_stdout=lambda _: None,
            on_stderr=lambda _: None,
            on_stage_start=lambda s, r, t: stages_started.append(s),
            on_stage_complete=lambda *_: None,
            on_waiting=lambda: waiting_event.set(),
        )

        task = asyncio.create_task(
            runner.run_experiment(
                config=config,
                tuning=tuning,
                secrets=EnvSecrets(),
                script_override=str(script),
            )
        )

        # Wait for first pause (after extract)
        await asyncio.wait_for(waiting_event.wait(), timeout=10)
        waiting_event.clear()
        assert stages_started == ["extract"]

        # Advance to validate
        runner.advance()

        # Wait for second pause (after validate)
        await asyncio.wait_for(waiting_event.wait(), timeout=10)
        assert stages_started == ["extract", "validate"]

        await runner.cancel()
        await task

    @pytest.mark.asyncio
    async def test_skip_proceeds_to_next_stage(self, project_dir: Path):
        script = project_dir / "noop.py"
        script.write_text("print('done')\n")

        config = PipelineAppConfig(
            python_path="python3",
            project_root=str(project_dir),
        )
        tuning = TuningConfig(
            auto_advance=False,
            pdf_path=str(project_dir),
            gold_standard_path=str(project_dir / "gs.csv"),
        )

        waiting_event = asyncio.Event()
        stages_started: list[str] = []

        lock = SubprocessLock()
        runner = TuningRunner(lock)
        runner.set_callbacks(
            on_stdout=lambda _: None,
            on_stderr=lambda _: None,
            on_stage_start=lambda s, r, t: stages_started.append(s),
            on_stage_complete=lambda *_: None,
            on_waiting=lambda: waiting_event.set(),
        )

        task = asyncio.create_task(
            runner.run_experiment(
                config=config,
                tuning=tuning,
                secrets=EnvSecrets(),
                script_override=str(script),
            )
        )

        # Wait for first pause
        await asyncio.wait_for(waiting_event.wait(), timeout=10)
        waiting_event.clear()

        # Skip
        runner.skip()

        # Wait for second pause
        await asyncio.wait_for(waiting_event.wait(), timeout=10)
        assert len(stages_started) == 2

        await runner.cancel()
        await task


class TestTuningRunnerMultiRepeat:
    @pytest.mark.asyncio
    async def test_multi_repeat_runs_all_stages_per_repeat(
        self,
        project_dir: Path,
    ):
        script = project_dir / "noop.py"
        script.write_text("print('done')\n")

        config = PipelineAppConfig(
            python_path="python3",
            project_root=str(project_dir),
        )
        tuning = TuningConfig(
            auto_advance=True,
            repeats=2,
            pdf_path=str(project_dir),
            gold_standard_path=str(project_dir / "gs.csv"),
        )

        stage_starts: list[tuple[str, int, int]] = []

        lock = SubprocessLock()
        runner = TuningRunner(lock)
        runner.set_callbacks(
            on_stdout=lambda _: None,
            on_stderr=lambda _: None,
            on_stage_start=lambda s, r, t: stage_starts.append((s, r, t)),
            on_stage_complete=lambda *_: None,
            on_waiting=lambda: None,
        )

        await runner.run_experiment(
            config=config,
            tuning=tuning,
            secrets=EnvSecrets(),
            script_override=str(script),
        )

        assert len(stage_starts) == len(TUNING_STAGES) * 2
        repeat_1 = [s for s, r, _ in stage_starts if r == 1]
        repeat_2 = [s for s, r, _ in stage_starts if r == 2]
        assert len(repeat_1) == len(TUNING_STAGES)
        assert len(repeat_2) == len(TUNING_STAGES)
        assert all(t == 2 for _, _, t in stage_starts)


class TestTuningRunnerActivityState:
    """Regression tests for the public is_active / is_waiting properties."""

    def test_initially_not_active(self):
        runner = TuningRunner(SubprocessLock())
        assert runner.is_active is False

    def test_initially_not_waiting(self):
        runner = TuningRunner(SubprocessLock())
        assert runner.is_waiting is False

    @pytest.mark.asyncio
    async def test_is_waiting_true_between_stages(self, project_dir: Path):
        script = project_dir / "noop.py"
        script.write_text("print('done')\n")

        config = PipelineAppConfig(
            python_path="python3",
            project_root=str(project_dir),
        )
        tuning = TuningConfig(
            auto_advance=False,
            pdf_path=str(project_dir),
            gold_standard_path=str(project_dir / "gs.csv"),
        )

        waiting_event = asyncio.Event()
        runner = TuningRunner(SubprocessLock())
        runner.set_callbacks(
            on_stdout=lambda _: None,
            on_stderr=lambda _: None,
            on_stage_start=lambda *_: None,
            on_stage_complete=lambda *_: None,
            on_waiting=lambda: waiting_event.set(),
        )

        task = asyncio.create_task(
            runner.run_experiment(
                config=config,
                tuning=tuning,
                secrets=EnvSecrets(),
                script_override=str(script),
            )
        )
        await asyncio.wait_for(waiting_event.wait(), timeout=10)

        assert runner.is_active is True
        assert runner.is_waiting is True

        await runner.cancel()
        await task

        assert runner.is_active is False
        assert runner.is_waiting is False

    @pytest.mark.asyncio
    async def test_is_waiting_false_after_advance(self, project_dir: Path):
        script = project_dir / "noop.py"
        script.write_text("print('done')\n")

        config = PipelineAppConfig(
            python_path="python3",
            project_root=str(project_dir),
        )
        tuning = TuningConfig(
            auto_advance=False,
            pdf_path=str(project_dir),
            gold_standard_path=str(project_dir / "gs.csv"),
        )

        waiting_event = asyncio.Event()
        runner = TuningRunner(SubprocessLock())
        runner.set_callbacks(
            on_stdout=lambda _: None,
            on_stderr=lambda _: None,
            on_stage_start=lambda *_: None,
            on_stage_complete=lambda *_: None,
            on_waiting=lambda: waiting_event.set(),
        )

        task = asyncio.create_task(
            runner.run_experiment(
                config=config,
                tuning=tuning,
                secrets=EnvSecrets(),
                script_override=str(script),
            )
        )
        await asyncio.wait_for(waiting_event.wait(), timeout=10)
        waiting_event.clear()
        runner.advance()
        # Wait until the next pause to confirm wait flag toggled correctly.
        await asyncio.wait_for(waiting_event.wait(), timeout=10)
        assert runner.is_waiting is True

        await runner.cancel()
        await task

    @pytest.mark.asyncio
    async def test_completed_experiment_clears_state(self, project_dir: Path):
        script = project_dir / "noop.py"
        script.write_text("print('done')\n")

        config = PipelineAppConfig(
            python_path="python3",
            project_root=str(project_dir),
        )
        tuning = TuningConfig(
            auto_advance=True,
            pdf_path=str(project_dir),
            gold_standard_path=str(project_dir / "gs.csv"),
        )

        runner = TuningRunner(SubprocessLock())
        runner.set_callbacks(
            on_stdout=lambda _: None,
            on_stderr=lambda _: None,
            on_stage_start=lambda *_: None,
            on_stage_complete=lambda *_: None,
            on_waiting=lambda: None,
        )

        await runner.run_experiment(
            config=config,
            tuning=tuning,
            secrets=EnvSecrets(),
            script_override=str(script),
        )
        assert runner.is_active is False
        assert runner.is_waiting is False


class TestBuildExtractConfigEdgeCases:
    def test_llm_max_tokens_from_tuning_when_not_main(self):
        main = PipelineAppConfig(llm_max_tokens=64000)
        tuning = TuningConfig(
            use_main_config=False,
            llm_max_tokens=32000,
        )
        result = build_extract_config(main, tuning)
        assert result.llm_max_tokens == 32000

    def test_prompt_version_from_tuning_when_not_main(self):
        main = PipelineAppConfig(prompt_version="v5")
        tuning = TuningConfig(
            use_main_config=False,
            prompt_version="v3",
        )
        result = build_extract_config(main, tuning)
        assert result.prompt_version == "v3"
