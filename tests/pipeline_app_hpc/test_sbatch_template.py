"""Tests verifying the sbatch template renders correctly."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from pipeline_app_hpc.config import HpcAppConfig


class TestSbatchTemplate:
    def test_template_exists(self):
        path = (
            Path(__file__).parent.parent.parent
            / "pipeline_app_hpc"
            / "sbatch"
            / "vllm_serve.sbatch.j2"
        )
        assert path.is_file()

    def test_render_substitutes_account_partition_qos(self):
        from pipeline_app_hpc.hpc.lifecycle import _render_template

        cfg = HpcAppConfig(
            vllm_account="my-acct",
            vllm_partition="gpu-test",
            vllm_qos="qos9",
            vllm_time_limit="02:30:00",
            vllm_cpus_per_task=8,
            vllm_mem="32G",
            vllm_remote_log_dir="/tmp/logs",
            vllm_remote_venv_path="/opt/venv",
            vllm_hf_home="/opt/hf",
        )
        out = _render_template(cfg)
        assert "#SBATCH --account=my-acct" in out
        assert "#SBATCH --partition=gpu-test" in out
        assert "#SBATCH --qos=qos9" in out
        assert "#SBATCH --time=02:30:00" in out
        assert "#SBATCH --cpus-per-task=8" in out
        assert "#SBATCH --mem=32G" in out
        assert "/tmp/logs" in out
        assert "/opt/venv/bin/activate" in out
        assert "/opt/hf" in out
        assert "##VLLM_PORT=" in out  # the marker echo line
        assert "ulimit -n 65536" in out

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("vllm_account", "acct\n#SBATCH --partition=other"),
            ("vllm_remote_workdir", "relative/work"),
            ("vllm_remote_log_dir", '/tmp/logs"; touch /tmp/pwned; "'),
            ("vllm_remote_venv_path", "relative/venv"),
        ],
    )
    def test_render_rejects_unsafe_template_values(self, field, value):
        from pipeline_app_hpc.hpc.lifecycle import _render_template

        cfg = HpcAppConfig(**{field: value})
        with pytest.raises(ValueError, match=field):
            _render_template(cfg)

    @pytest.mark.asyncio
    async def test_rsync_template_uses_non_colliding_heredoc_marker(self):
        from pipeline_app_hpc.hpc.lifecycle import rsync_sbatch_template

        ssh = MagicMock()
        ssh.run_bash = AsyncMock()

        rendered = "before\nCSVD_HPC_EOF\nafter"
        await rsync_sbatch_template(ssh, "/remote/work", rendered)

        cmd = ssh.run_bash.call_args.args[0]
        assert "<< 'CSVD_HPC_EOF'\n" not in cmd
        assert rendered in cmd
        assert cmd.endswith("chmod +x /remote/work/vllm_serve.sbatch")

    @pytest.mark.asyncio
    async def test_rsync_template_aborts_chmod_on_cat_failure(self):
        # `bash -lc` does not enable `set -e` by default, so a heredoc-write
        # failure (e.g. NFS quota exceeded mid-cat) used to silently fall
        # through to the chmod line and exit 0 with a truncated sbatch on
        # disk. The remote command must include `set -e` so any failure in
        # mkdir/cat/chmod aborts the whole sequence and run_bash(check=True)
        # surfaces it to the lifecycle.
        from pipeline_app_hpc.hpc.lifecycle import rsync_sbatch_template

        ssh = MagicMock()
        ssh.run_bash = AsyncMock()

        await rsync_sbatch_template(ssh, "/remote/work", "body")

        cmd = ssh.run_bash.call_args.args[0]
        assert cmd.startswith("set -e\n"), (
            f"rsync_sbatch_template must prefix the command with `set -e` "
            f"to abort on cat/chmod failure; got: {cmd!r}"
        )
