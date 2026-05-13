"""Tests for pipeline_app_hpc.runner."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


class TestBuildExtractEnv:
    def test_includes_vllm_url_and_model(self):
        from pipeline_app_hpc.config import HpcAppConfig
        from pipeline_app_hpc.runner import build_extract_env

        cfg = HpcAppConfig(
            vllm_local_port=31000,
            vllm_adapter_name="svd",
            vllm_adapter_path="/fake/adapter",
        )
        env = build_extract_env(
            cfg,
            secrets=MagicMock(ncbi_api_key="abc", entrez_email="e@e"),
            local_url="http://127.0.0.1:31000",
        )
        assert env["VLLM_BASE_URL"] == "http://127.0.0.1:31000"
        assert env["VLLM_MODEL"] == "svd"
        assert env["NCBI_API_KEY"] == "abc"
        assert env["ENTREZ_EMAIL"] == "e@e"
        # Pipeline overrides
        assert "PIPELINE_CONFIDENCE_THRESHOLD" in env

    def test_uses_base_model_when_no_adapter(self):
        from pipeline_app_hpc.config import HpcAppConfig
        from pipeline_app_hpc.runner import build_extract_env

        cfg = HpcAppConfig(
            vllm_adapter_path="",
            vllm_base_model="unsloth/gemma-4-31b-it-unsloth-bnb-4bit",
        )
        env = build_extract_env(
            cfg,
            secrets=MagicMock(ncbi_api_key="", entrez_email=""),
            local_url="http://127.0.0.1:30800",
        )
        assert env["VLLM_MODEL"] == "unsloth/gemma-4-31b-it-unsloth-bnb-4bit"

    def test_secrets_omitted_when_empty(self):
        from pipeline_app_hpc.config import HpcAppConfig
        from pipeline_app_hpc.runner import build_extract_env

        env = build_extract_env(
            HpcAppConfig(),
            secrets=MagicMock(ncbi_api_key="", entrez_email=""),
            local_url="http://127.0.0.1:30800",
        )
        assert "NCBI_API_KEY" not in env
        assert "ENTREZ_EMAIL" not in env

    def test_includes_rate_limit_and_progress_overrides(self):
        from pipeline_app_hpc.config import HpcAppConfig
        from pipeline_app_hpc.runner import build_extract_env

        cfg = HpcAppConfig(
            estimated_tokens_per_call=12_345,
            ncbi_rate_limit=7,
            progress_file="/tmp/progress.json",
        )
        env = build_extract_env(
            cfg,
            secrets=MagicMock(ncbi_api_key="", entrez_email=""),
            local_url="http://127.0.0.1:30800",
        )
        assert env["PIPELINE_ESTIMATED_TOKENS_PER_CALL"] == "12345"
        assert env["PIPELINE_NCBI_RATE_LIMIT"] == "7"
        assert env["PIPELINE_PROGRESS_FILE"] == "/tmp/progress.json"


class TestBuildExtractArgs:
    def test_local_pdfs_path_required(self):
        from pipeline_app_hpc.config import HpcAppConfig
        from pipeline_app_hpc.runner import build_extract_args

        cfg = HpcAppConfig(local_pdfs_path="/data/pdfs")
        args = build_extract_args(cfg)
        assert "--local-pdfs" in args
        assert "/data/pdfs" in args

    def test_skip_validation_flag(self):
        from pipeline_app_hpc.config import HpcAppConfig
        from pipeline_app_hpc.runner import build_extract_args

        cfg = HpcAppConfig(local_pdfs_path="/d", skip_validation=True)
        args = build_extract_args(cfg)
        assert "--skip-validation" in args


class TestPipelineRunnerPrecondition:
    @pytest.mark.asyncio
    async def test_refuses_to_run_when_vllm_not_ready(self):
        from pipeline_app_hpc.config import EnvSecrets, HpcAppConfig
        from pipeline_app_hpc.hpc.lifecycle import (
            VllmServer,
            VllmServerSnapshot,
            VllmServerState,
        )
        from pipeline_app_hpc.runner import PipelineRunner, SubprocessLock

        srv = MagicMock(spec=VllmServer)
        srv.snapshot = VllmServerSnapshot(
            state=VllmServerState.IDLE,
            job_id=None,
            node=None,
            local_url=None,
            time_left_seconds=None,
            error=None,
            last_log_tail="",
        )
        lock = SubprocessLock()
        runner = PipelineRunner(lock=lock, vllm_server=srv)
        with pytest.raises(RuntimeError, match="vLLM is not READY"):
            await runner.run(config=HpcAppConfig(), secrets=EnvSecrets())
