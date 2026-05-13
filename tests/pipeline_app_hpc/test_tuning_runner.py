"""Tests for the TuningRunner."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestTuningRunner:
    def test_build_tuning_extract_config_applies_tuning_overrides(self):
        from pipeline_app_hpc.config import HpcAppConfig, TuningConfig
        from pipeline_app_hpc.runner import build_tuning_extract_config

        cfg = HpcAppConfig(
            local_pdfs_path="/main/pdfs",
            skip_validation=False,
            confidence_threshold=0.5,
            prompt_version="ollama_v1",
            vllm_base_model="main-model",
        )
        tuning = TuningConfig(
            pdf_path="/tuning/pdfs",
            confidence_threshold=0.8,
            use_main_config=False,
            prompt_version="gemma_v5",
            vllm_base_model="tuning-model",
            vllm_adapter_path="/adapter",
            vllm_adapter_name="svd",
        )

        out = build_tuning_extract_config(cfg, tuning)

        assert out.local_pdfs_path == "/tuning/pdfs"
        assert out.skip_validation is True
        assert out.confidence_threshold == 0.8
        assert out.prompt_version == "gemma_v5"
        assert out.vllm_base_model == "tuning-model"
        assert out.vllm_adapter_path == "/adapter"
        assert out.vllm_adapter_name == "svd"

    @pytest.mark.asyncio
    async def test_extract_stage_uses_pipeline_app_hpc_cli(
        self, mocker, project_dir: Path
    ):
        from pipeline_app_hpc.config import (
            EnvSecrets,
            HpcAppConfig,
            TuningConfig,
        )
        from pipeline_app_hpc.hpc.lifecycle import (
            VllmServer,
            VllmServerSnapshot,
            VllmServerState,
        )
        from pipeline_app_hpc.runner import (
            SubprocessLock,
            TuningRunner,
        )

        srv = MagicMock(spec=VllmServer)
        srv.snapshot = VllmServerSnapshot(
            state=VllmServerState.READY,
            job_id="1",
            node="sphpc-gpu05",
            local_url="http://127.0.0.1:30800",
            time_left_seconds=3600,
            error=None,
            last_log_tail="",
        )
        lock = SubprocessLock()
        runner = TuningRunner(lock=lock, vllm_server=srv)

        run_mock = mocker.patch(
            "pipeline_app_hpc.runner._run_process_streamed",
            new=AsyncMock(
                return_value=MagicMock(exit_code=0, report_path="/tmp/x.json")
            ),
        )

        await runner.run_stage(
            stage="extract",
            config=HpcAppConfig(
                project_root=str(project_dir),
                local_pdfs_path="/tmp/pdfs",
            ),
            tuning_config=TuningConfig(),
            secrets=EnvSecrets(),
        )

        argv = run_mock.call_args.kwargs["argv"]
        assert argv[1:4] == ["-m", "pipeline_app_hpc.cli", "--local-pdfs"]
        assert "--skip-validation" in argv

    @pytest.mark.asyncio
    async def test_validate_stage_invokes_validate_pipeline_script(
        self, mocker, project_dir: Path
    ):
        from pipeline_app_hpc.config import (
            EnvSecrets,
            HpcAppConfig,
            TuningConfig,
        )
        from pipeline_app_hpc.runner import (
            SubprocessLock,
            TuningRunner,
        )

        runner = TuningRunner(lock=SubprocessLock(), vllm_server=MagicMock())
        run_mock = mocker.patch(
            "pipeline_app_hpc.runner._run_process_streamed",
            new=AsyncMock(return_value=MagicMock(exit_code=0)),
        )

        await runner.run_stage(
            stage="validate",
            config=HpcAppConfig(project_root=str(project_dir)),
            tuning_config=TuningConfig(
                gold_standard_path="/tmp/gold.csv",
            ),
            secrets=EnvSecrets(),
            report_path="/tmp/report.json",
        )

        argv = run_mock.call_args.kwargs["argv"]
        assert "scripts/validate_pipeline.py" in " ".join(argv)
        assert "/tmp/report.json" in argv
        assert "/tmp/gold.csv" in argv
        assert "--local-pdfs" in argv

    @pytest.mark.asyncio
    async def test_validate_stage_requires_report_path(self, project_dir: Path):
        from pipeline_app_hpc.config import EnvSecrets, HpcAppConfig, TuningConfig
        from pipeline_app_hpc.runner import SubprocessLock, TuningRunner

        runner = TuningRunner(lock=SubprocessLock(), vllm_server=MagicMock())

        with pytest.raises(ValueError, match="report_path is required"):
            await runner.run_stage(
                stage="validate",
                config=HpcAppConfig(project_root=str(project_dir)),
                tuning_config=TuningConfig(gold_standard_path="/tmp/gold.csv"),
                secrets=EnvSecrets(),
                report_path=None,
            )
