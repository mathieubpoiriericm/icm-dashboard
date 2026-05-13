"""Tests for pipeline_app_hpc.hpc.sbatch."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def _ssh_result(returncode: int = 0, stdout: str = "", stderr: str = ""):
    from pipeline_app_hpc.hpc.ssh import SshResult

    return SshResult(returncode=returncode, stdout=stdout, stderr=stderr)


class TestJobInfo:
    def test_dataclass_fields(self):
        from pipeline_app_hpc.hpc.sbatch import JobInfo

        j = JobInfo(
            job_id="123",
            state="RUNNING",
            node="sphpc-gpu05",
            time_left_seconds=3600,
            elapsed_seconds=120,
        )
        assert j.job_id == "123"
        assert j.state == "RUNNING"


class TestParseSqueueLine:
    def test_running_with_node(self):
        from pipeline_app_hpc.hpc.sbatch import parse_squeue_line

        info = parse_squeue_line("123|RUNNING|sphpc-gpu05|3:30:00|0:05:00")
        assert info.job_id == "123"
        assert info.state == "RUNNING"
        assert info.node == "sphpc-gpu05"
        assert info.time_left_seconds == 3 * 3600 + 30 * 60
        assert info.elapsed_seconds == 5 * 60

    def test_pending_with_reason(self):
        from pipeline_app_hpc.hpc.sbatch import parse_squeue_line

        info = parse_squeue_line("124|PENDING|(Resources)|UNLIMITED|0:00")
        assert info.state == "PENDING"
        assert info.node is None

    def test_completed_no_time_left(self):
        from pipeline_app_hpc.hpc.sbatch import parse_squeue_line

        info = parse_squeue_line("125|COMPLETED|sphpc-gpu05|0:00|1:00:00")
        assert info.state == "COMPLETED"
        assert info.time_left_seconds == 0

    def test_days_in_time(self):
        from pipeline_app_hpc.hpc.sbatch import parse_squeue_line

        info = parse_squeue_line("1|RUNNING|sphpc-gpu05|1-12:00:00|0:01")
        assert info.time_left_seconds == 24 * 3600 + 12 * 3600


class TestSubmitVllmJob:
    @pytest.mark.asyncio
    async def test_submit_returns_job_id(self):
        from pipeline_app_hpc.hpc.sbatch import submit_vllm_job

        ssh = MagicMock()
        ssh.run_bash = AsyncMock(
            return_value=_ssh_result(stdout="Submitted batch job 2398127\n")
        )

        job_id = await submit_vllm_job(
            ssh,
            sbatch_remote_path="/opt/csvd-hpc/vllm_serve.sbatch",
            workdir="/opt/csvd-hpc",
            env={"VLLM_BASE_MODEL": "google/gemma-4-31b-it"},
        )
        assert job_id == "2398127"

    @pytest.mark.asyncio
    async def test_submit_builds_export_argument(self):
        from pipeline_app_hpc.hpc.sbatch import submit_vllm_job

        ssh = MagicMock()
        ssh.run_bash = AsyncMock(
            return_value=_ssh_result(stdout="Submitted batch job 1\n")
        )
        await submit_vllm_job(
            ssh,
            sbatch_remote_path="/opt/csvd-hpc/vllm_serve.sbatch",
            workdir="/opt/csvd-hpc",
            env={"A": "alpha", "B": "/path/to/b"},
        )
        cmd = ssh.run_bash.call_args.args[0]
        assert "cd /opt/csvd-hpc" in cmd
        assert "sbatch" in cmd
        assert "--export=ALL,A=alpha,B=/path/to/b" in cmd
        assert "/opt/csvd-hpc/vllm_serve.sbatch" in cmd

    @pytest.mark.asyncio
    async def test_submit_quotes_value_with_single_quote(self):
        from pipeline_app_hpc.hpc.sbatch import submit_vllm_job

        ssh = MagicMock()
        ssh.run_bash = AsyncMock(
            return_value=_ssh_result(stdout="Submitted batch job 1\n")
        )

        await submit_vllm_job(
            ssh,
            sbatch_remote_path="/x.sbatch",
            workdir="/wd",
            env={"ADAPTER": "/path/has'quote"},
        )

        cmd = ssh.run_bash.call_args.args[0]
        assert "ADAPTER=" in cmd
        assert "/path/has" in cmd
        assert "quote" in cmd

    @pytest.mark.asyncio
    async def test_submit_rejects_value_with_comma(self):
        from pipeline_app_hpc.hpc.sbatch import submit_vllm_job

        ssh = MagicMock()
        with pytest.raises(ValueError, match="comma or newline"):
            await submit_vllm_job(
                ssh,
                sbatch_remote_path="/x.sbatch",
                workdir="/wd",
                env={"BAD": "has,comma"},
            )

    @pytest.mark.asyncio
    async def test_submit_rejects_invalid_env_name(self):
        from pipeline_app_hpc.hpc.sbatch import submit_vllm_job

        ssh = MagicMock()
        with pytest.raises(ValueError, match="invalid sbatch export env var name"):
            await submit_vllm_job(
                ssh,
                sbatch_remote_path="/x.sbatch",
                workdir="/wd",
                env={"BAD;touch /tmp/pwned": "x"},
            )

    @pytest.mark.asyncio
    async def test_submit_unparseable_output_raises(self):
        from pipeline_app_hpc.hpc.sbatch import submit_vllm_job

        ssh = MagicMock()
        ssh.run_bash = AsyncMock(return_value=_ssh_result(stdout="weird output"))
        with pytest.raises(RuntimeError, match="could not parse"):
            await submit_vllm_job(
                ssh,
                sbatch_remote_path="/x.sbatch",
                workdir="/wd",
                env={},
            )


class TestGetJobInfo:
    @pytest.mark.asyncio
    async def test_get_job_info_running(self):
        from pipeline_app_hpc.hpc.sbatch import get_job_info

        ssh = MagicMock()
        ssh.run_bash = AsyncMock(
            return_value=_ssh_result(stdout="123|RUNNING|sphpc-gpu05|3:00:00|0:05:00\n")
        )
        info = await get_job_info(ssh, "123")
        assert info.state == "RUNNING"
        assert info.node == "sphpc-gpu05"

    @pytest.mark.asyncio
    async def test_get_job_info_pipe_chars_safe(self):
        """The squeue -o format string must reach the remote shell unchanged.

        Regression: argv-style ssh.run() joins args with spaces and the
        remote login shell parses the result, so '%i|%T|...' becomes a
        pipeline. get_job_info must route through run_bash to keep the
        format string single-quoted across the wire.
        """
        from pipeline_app_hpc.hpc.sbatch import get_job_info

        ssh = MagicMock()
        ssh.run_bash = AsyncMock(
            return_value=_ssh_result(stdout="42|RUNNING|node|1:00|0:01\n")
        )
        await get_job_info(ssh, "42")
        cmd = ssh.run_bash.call_args.args[0]
        assert "'%i|%T|%R|%L|%M'" in cmd
        assert cmd.endswith("--job 42")

    @pytest.mark.asyncio
    async def test_get_job_info_missing_job_falls_back_to_sacct(self):
        """squeue empty → sacct lookup. sacct returning FAILED must surface."""
        from pipeline_app_hpc.hpc.sbatch import get_job_info

        ssh = MagicMock()

        async def _run_bash(cmd: str, **_: object):
            if cmd.startswith("squeue"):
                return _ssh_result(stdout="")
            if cmd.startswith("sacct"):
                return _ssh_result(stdout="999|FAILED\n")
            raise AssertionError(f"unexpected: {cmd}")

        ssh.run_bash = AsyncMock(side_effect=_run_bash)
        info = await get_job_info(ssh, "999")
        assert info.state == "FAILED"
        assert info.job_id == "999"

    @pytest.mark.asyncio
    async def test_get_job_info_squeue_rc1_treated_as_missing(self):
        """squeue exits 1 for purged jobs on tightly configured clusters."""
        from pipeline_app_hpc.hpc.sbatch import get_job_info

        ssh = MagicMock()

        async def _run_bash(cmd: str, **_: object):
            if cmd.startswith("squeue"):
                return _ssh_result(returncode=1, stdout="")
            return _ssh_result(stdout="")

        ssh.run_bash = AsyncMock(side_effect=_run_bash)
        info = await get_job_info(ssh, "999")
        assert info.state == "COMPLETED"

    @pytest.mark.asyncio
    async def test_get_job_info_squeue_failure_raises(self):
        from pipeline_app_hpc.hpc.sbatch import get_job_info

        ssh = MagicMock()
        ssh.run_bash = AsyncMock(
            return_value=_ssh_result(returncode=2, stderr="auth")
        )
        with pytest.raises(RuntimeError, match="squeue failed"):
            await get_job_info(ssh, "999")


class TestScancel:
    @pytest.mark.asyncio
    async def test_scancel_calls_ssh(self):
        from pipeline_app_hpc.hpc.sbatch import scancel

        ssh = MagicMock()
        ssh.run_bash = AsyncMock(return_value=_ssh_result())
        await scancel(ssh, "555")
        cmd = ssh.run_bash.call_args.args[0]
        assert cmd.startswith("scancel ")
        assert "555" in cmd


class TestFetchLogTail:
    @pytest.mark.asyncio
    async def test_fetch_log_tail_returns_stdout(self):
        from pipeline_app_hpc.hpc.sbatch import fetch_log_tail

        ssh = MagicMock()
        ssh.run_bash = AsyncMock(return_value=_ssh_result(stdout="line1\nline2\n"))
        out = await fetch_log_tail(ssh, "/path/to/file.err", lines=2)
        assert out == "line1\nline2\n"
        cmd = ssh.run_bash.call_args.args[0]
        assert cmd == "tail -n 2 /path/to/file.err"
