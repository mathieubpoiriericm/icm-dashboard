"""Integration tests for subprocess execution, cancel, report detection."""

from __future__ import annotations

import asyncio
import os
import shutil
import stat
import sys
import time
from pathlib import Path

import pytest
from pipeline_app.config import EnvSecrets, PipelineAppConfig
from pipeline_app.runner import (
    PipelineRunner,
    SubprocessLock,
    _find_newest_file,
    _run_process_streamed,
    find_newest_report,
    validate_python_path,
    validate_rscript_path,
)


class TestFindNewestReport:
    def test_finds_report_created_after_start(self, tmp_path: Path):
        json_dir = tmp_path / "json"
        json_dir.mkdir()
        report = json_dir / "pipeline_report_2026-01-01.json"
        report.write_text("{}")
        result = find_newest_report(tmp_path, time.time() - 10)
        assert result == report

    def test_returns_none_when_no_json_dir(self, tmp_path: Path):
        assert find_newest_report(tmp_path, time.time()) is None

    def test_returns_none_when_no_reports(self, tmp_path: Path):
        json_dir = tmp_path / "json"
        json_dir.mkdir()
        assert find_newest_report(tmp_path, time.time() + 100) is None

    def test_ignores_non_report_json(self, tmp_path: Path):
        json_dir = tmp_path / "json"
        json_dir.mkdir()
        (json_dir / "other_file.json").write_text("{}")
        assert find_newest_report(tmp_path, time.time() - 10) is None

    def test_returns_newest_when_multiple(self, tmp_path: Path):
        json_dir = tmp_path / "json"
        json_dir.mkdir()
        old = json_dir / "pipeline_report_old.json"
        old.write_text("{}")
        os.utime(old, (time.time() - 60, time.time() - 60))
        new = json_dir / "pipeline_report_new.json"
        new.write_text("{}")
        result = find_newest_report(tmp_path, time.time() - 120)
        assert result == new


class TestSubprocessLock:
    def test_not_running_initially(self):
        lock = SubprocessLock()
        assert lock.is_running is False

    @pytest.mark.asyncio
    async def test_cancel_when_not_running(self):
        lock = SubprocessLock()
        await lock.cancel()


class TestPipelineRunner:
    @pytest.mark.asyncio
    async def test_runs_simple_command(self, project_dir: Path):
        script = project_dir / "test_script.py"
        script.write_text(
            "import sys\nprint('hello stdout')\nprint('error line', file=sys.stderr)\n"
        )
        config = PipelineAppConfig(
            python_path="python3",
            project_root=str(project_dir),
        )
        lock = SubprocessLock()
        runner = PipelineRunner(lock)

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        stages: list[str] = []

        result = await runner.run(
            config=config,
            secrets=EnvSecrets(),
            on_stdout=stdout_lines.append,
            on_stderr=stderr_lines.append,
            on_stage=stages.append,
            cli_args_override=[str(script)],
        )
        assert result.exit_code == 0
        assert "hello stdout" in stdout_lines
        assert any("error line" in line for line in stderr_lines)

    @pytest.mark.asyncio
    async def test_parses_stage_markers(self, project_dir: Path):
        script = project_dir / "stage_script.py"
        script.write_text(
            "print('##STAGE:search##', flush=True)\n"
            "print('searching...')\n"
            "print('##STAGE:extract##', flush=True)\n"
        )
        config = PipelineAppConfig(
            python_path="python3",
            project_root=str(project_dir),
        )
        lock = SubprocessLock()
        runner = PipelineRunner(lock)

        stdout_lines: list[str] = []
        stages: list[str] = []

        await runner.run(
            config=config,
            secrets=EnvSecrets(),
            on_stdout=stdout_lines.append,
            on_stderr=lambda _: None,
            on_stage=stages.append,
            cli_args_override=[str(script)],
        )
        assert stages == ["search", "extract"]
        assert "searching..." in stdout_lines

    @pytest.mark.asyncio
    async def test_cancel_terminates_process(self, project_dir: Path):
        script = project_dir / "long_script.py"
        script.write_text("import time\ntime.sleep(60)\n")
        config = PipelineAppConfig(
            python_path="python3",
            project_root=str(project_dir),
        )
        lock = SubprocessLock()
        runner = PipelineRunner(lock)

        task = asyncio.create_task(
            runner.run(
                config=config,
                secrets=EnvSecrets(),
                on_stdout=lambda _: None,
                on_stderr=lambda _: None,
                on_stage=lambda _: None,
                cli_args_override=[str(script)],
            )
        )
        await asyncio.sleep(0.5)
        assert lock.is_running
        await lock.cancel()
        result = await task
        assert result.exit_code != 0

    @pytest.mark.asyncio
    async def test_prevents_concurrent_runs(self, project_dir: Path):
        script = project_dir / "slow.py"
        script.write_text("import time\ntime.sleep(5)\n")
        config = PipelineAppConfig(
            python_path="python3",
            project_root=str(project_dir),
        )
        lock = SubprocessLock()
        runner = PipelineRunner(lock)

        task = asyncio.create_task(
            runner.run(
                config=config,
                secrets=EnvSecrets(),
                on_stdout=lambda _: None,
                on_stderr=lambda _: None,
                on_stage=lambda _: None,
                cli_args_override=[str(script)],
            )
        )
        await asyncio.sleep(0.3)

        with pytest.raises(RuntimeError, match="already running"):
            await runner.run(
                config=config,
                secrets=EnvSecrets(),
                on_stdout=lambda _: None,
                on_stderr=lambda _: None,
                on_stage=lambda _: None,
                cli_args_override=[str(script)],
            )

        await lock.cancel()
        await task


class TestSubprocessLockGuard:
    @pytest.mark.asyncio
    async def test_run_guard_sets_running_state(self):
        lock = SubprocessLock()
        assert lock.is_running is False
        async with lock.run_guard():
            assert lock.is_running is True
        assert lock.is_running is False

    @pytest.mark.asyncio
    async def test_run_guard_clears_process_on_exit(self):
        lock = SubprocessLock()
        async with lock.run_guard():
            lock.set_process("fake_process")  # ty: ignore[invalid-argument-type]
        assert lock._process is None

    @pytest.mark.asyncio
    async def test_run_guard_clears_state_on_exception(self):
        lock = SubprocessLock()
        with pytest.raises(ValueError):
            async with lock.run_guard():
                raise ValueError("test error")
        assert lock.is_running is False
        assert lock._process is None

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_cancel_escalates_to_sigkill(self):
        """Process that ignores SIGTERM gets SIGKILL after 5 seconds."""
        lock = SubprocessLock()
        process = await asyncio.create_subprocess_exec(
            "python3",
            "-c",
            "import signal, sys, time; "
            "signal.signal(signal.SIGTERM, lambda *_: None); "
            "print('ready', flush=True); "
            "time.sleep(60)",
            stdout=asyncio.subprocess.PIPE,
        )
        # Wait for the process to install the signal handler
        assert process.stdout is not None
        line = await asyncio.wait_for(process.stdout.readline(), timeout=5)
        assert line.strip() == b"ready"
        async with lock.run_guard():
            lock.set_process(process)
            await lock.cancel()
        assert process.returncode is not None


class TestFindNewestFile:
    def test_returns_none_for_nonexistent_dir(self):
        result = _find_newest_file(
            Path("/nonexistent_dir"),
            "prefix_",
            time.time(),
        )
        assert result is None

    def test_finds_file_with_matching_prefix(self, tmp_path: Path):
        f = tmp_path / "prefix_2026.csv"
        f.write_text("data")
        result = _find_newest_file(tmp_path, "prefix_", time.time() - 10)
        assert result == f

    def test_ignores_files_without_prefix(self, tmp_path: Path):
        (tmp_path / "other.csv").write_text("data")
        result = _find_newest_file(tmp_path, "prefix_", time.time() - 10)
        assert result is None

    def test_fudge_factor_includes_slightly_older_file(self, tmp_path: Path):
        """The -1 second fudge handles filesystem mtime inaccuracy."""
        f = tmp_path / "prefix_test.csv"
        f.write_text("data")
        now = time.time()
        os.utime(f, (now - 0.5, now - 0.5))
        result = _find_newest_file(tmp_path, "prefix_", now)
        assert result == f

    def test_excludes_much_older_file(self, tmp_path: Path):
        f = tmp_path / "prefix_test.csv"
        f.write_text("data")
        now = time.time()
        os.utime(f, (now - 5, now - 5))
        result = _find_newest_file(tmp_path, "prefix_", now)
        assert result is None

    def test_returns_newest_among_multiple(self, tmp_path: Path):
        old = tmp_path / "prefix_old.csv"
        old.write_text("data")
        os.utime(old, (time.time() - 60, time.time() - 60))
        new = tmp_path / "prefix_new.csv"
        new.write_text("data")
        result = _find_newest_file(tmp_path, "prefix_", time.time() - 120)
        assert result == new

    def test_skips_entries_that_fail_stat(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """A TOCTOU stat failure on one entry should not kill the search."""
        good = tmp_path / "prefix_keep.csv"
        good.write_text("data")
        doomed = tmp_path / "prefix_rotated.csv"
        doomed.write_text("data")

        real_stat = Path.stat

        def flaky_stat(self: Path, *args, **kwargs):
            if self == doomed:
                raise FileNotFoundError(str(self))
            return real_stat(self, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", flaky_stat)
        result = _find_newest_file(tmp_path, "prefix_", time.time() - 10)
        assert result == good

    def test_iterdir_permission_error_returns_none(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        def boom(self: Path):
            raise PermissionError(str(self))

        monkeypatch.setattr(Path, "iterdir", boom)
        result = _find_newest_file(tmp_path, "prefix_", time.time() - 10)
        assert result is None


class TestValidateRscriptPath:
    """Regression tests for validate_rscript_path (lazy plot-stage check)."""

    def test_resolves_bare_command_via_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ):
        fake = tmp_path / "Rscript"
        fake.write_text("#!/bin/sh\nexit 0\n")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        monkeypatch.setenv("PATH", str(tmp_path))
        result = validate_rscript_path()
        assert result == str(fake)

    def test_raises_when_not_in_path(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("PATH", "")
        if shutil.which("Rscript", path="") is not None:
            pytest.skip("Rscript resolvable even with empty PATH")
        with pytest.raises(ValueError, match="not found in PATH"):
            validate_rscript_path()

    def test_accepts_absolute_path(self, tmp_path: Path):
        fake = tmp_path / "my_rscript"
        fake.write_text("#!/bin/sh\nexit 0\n")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        result = validate_rscript_path(str(fake))
        assert result == os.path.abspath(str(fake))

    def test_rejects_nonexistent_absolute_path(self, tmp_path: Path):
        with pytest.raises(ValueError, match="does not exist"):
            validate_rscript_path(str(tmp_path / "missing"))

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX exec bit")
    def test_rejects_non_executable_absolute_path(self, tmp_path: Path):
        f = tmp_path / "not_exec"
        f.write_text("data")
        f.chmod(0o644)
        with pytest.raises(ValueError, match="not executable"):
            validate_rscript_path(str(f))


class TestValidatePythonPath:
    def test_resolves_bare_command_via_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ):
        fake = tmp_path / "python3"
        fake.write_text("#!/bin/sh\nexit 0\n")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        monkeypatch.setenv("PATH", str(tmp_path))
        result = validate_python_path("python3")
        assert result == str(fake)

    def test_rejects_invalid_bare_name(self):
        with pytest.raises(ValueError, match="Invalid Python executable name"):
            validate_python_path("not_python")

    def test_accepts_absolute_path(self, tmp_path: Path):
        fake = tmp_path / "python3.14"
        fake.write_text("#!/bin/sh\nexit 0\n")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        result = validate_python_path(str(fake))
        assert result == os.path.abspath(str(fake))

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlinks")
    def test_preserves_symlink_target_for_venv_python(self, tmp_path: Path):
        real_bin = tmp_path / "base" / "python3.14"
        real_bin.parent.mkdir()
        real_bin.write_text("#!/bin/sh\nexit 0\n")
        real_bin.chmod(real_bin.stat().st_mode | stat.S_IXUSR)

        venv_bin_dir = tmp_path / "venv" / "bin"
        venv_bin_dir.mkdir(parents=True)
        venv_python = venv_bin_dir / "python3.14"
        venv_python.symlink_to(real_bin)

        result = validate_python_path(str(venv_python))
        assert result == str(venv_python)
        assert result != str(real_bin)

    def test_rejects_nonexistent_absolute_path(self, tmp_path: Path):
        with pytest.raises(ValueError, match="does not exist"):
            validate_python_path(str(tmp_path / "missing_python"))

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX exec bit")
    def test_rejects_non_executable_absolute_path(self, tmp_path: Path):
        fake = tmp_path / "python3"
        fake.write_text("data")
        fake.chmod(0o644)
        with pytest.raises(ValueError, match="not executable"):
            validate_python_path(str(fake))

    def test_rejects_non_python_interpreter_name(self, tmp_path: Path):
        fake = tmp_path / "notpython"
        fake.write_text("#!/bin/sh\nexit 0\n")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        with pytest.raises(ValueError, match="Not a Python interpreter"):
            validate_python_path(str(fake))


class TestSubprocessLockCancelRace:
    """Regression tests for cancel arriving before set_process."""

    @pytest.mark.asyncio
    async def test_cancel_before_set_process_sets_flag(self):
        lock = SubprocessLock()
        async with lock.run_guard():
            assert lock._process is None
            await lock.cancel()
            assert lock._cancel_requested is True

    @pytest.mark.asyncio
    async def test_set_process_terminates_when_cancel_pending(self):
        lock = SubprocessLock()
        async with lock.run_guard():
            await lock.cancel()
            proc = await asyncio.create_subprocess_exec(
                "python3",
                "-c",
                "import time; time.sleep(60)",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                lock.set_process(proc)
                await asyncio.wait_for(proc.wait(), timeout=5.0)
                assert proc.returncode is not None
            finally:
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()

    @pytest.mark.asyncio
    async def test_cancel_flag_resets_for_next_run(self):
        lock = SubprocessLock()
        async with lock.run_guard():
            await lock.cancel()
            assert lock._cancel_requested is True
        assert lock._cancel_requested is False
        async with lock.run_guard():
            assert lock._cancel_requested is False

    @pytest.mark.asyncio
    async def test_cancel_outside_run_guard_is_noop(self):
        lock = SubprocessLock()
        await lock.cancel()
        assert lock._cancel_requested is False


class TestRunProcessStreamedCancel:
    """Regression: task-level cancel must escalate SIGTERM to SIGKILL on a
    subprocess that installs a no-op SIGTERM handler. Without escalation the
    child reparents to PID 1 when the app exits (start_new_session=True).
    """

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_sigkill_on_stubborn_subprocess(self):
        process = await asyncio.create_subprocess_exec(
            "python3",
            "-c",
            "import signal, sys, time; "
            "signal.signal(signal.SIGTERM, lambda *_: None); "
            "print('ready', flush=True); "
            "time.sleep(60)",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        assert process.stdout is not None
        line = await asyncio.wait_for(process.stdout.readline(), timeout=5)
        assert line.strip() == b"ready"

        task = asyncio.create_task(
            _run_process_streamed(
                process,
                on_stdout=lambda _: None,
                on_stderr=lambda _: None,
            )
        )
        # Let the streamer attach to the pipes before cancelling.
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # SIGTERM ignored, SIGKILL path fires after ~10s. Give 15s total.
        await asyncio.wait_for(process.wait(), timeout=15)
        assert process.returncode is not None


class TestPipelineRunnerReportPath:
    @pytest.mark.asyncio
    async def test_captures_report_path(self, project_dir: Path):
        script = project_dir / "make_report.py"
        script.write_text(
            "import os, json\n"
            "os.makedirs('logs/json', exist_ok=True)\n"
            "with open('logs/json/pipeline_report_test.json', 'w') as f:\n"
            "    json.dump({}, f)\n"
        )
        config = PipelineAppConfig(
            python_path="python3",
            project_root=str(project_dir),
        )
        lock = SubprocessLock()
        runner = PipelineRunner(lock)
        result = await runner.run(
            config=config,
            secrets=EnvSecrets(),
            on_stdout=lambda _: None,
            on_stderr=lambda _: None,
            on_stage=lambda _: None,
            cli_args_override=[str(script)],
        )
        assert result.exit_code == 0
        assert result.report_path is not None
        assert "pipeline_report_test.json" in result.report_path

    @pytest.mark.asyncio
    async def test_nonzero_exit_code(self, project_dir: Path):
        script = project_dir / "fail.py"
        script.write_text("import sys; sys.exit(1)\n")
        config = PipelineAppConfig(
            python_path="python3",
            project_root=str(project_dir),
        )
        lock = SubprocessLock()
        runner = PipelineRunner(lock)
        result = await runner.run(
            config=config,
            secrets=EnvSecrets(),
            on_stdout=lambda _: None,
            on_stderr=lambda _: None,
            on_stage=lambda _: None,
            cli_args_override=[str(script)],
        )
        assert result.exit_code == 1
