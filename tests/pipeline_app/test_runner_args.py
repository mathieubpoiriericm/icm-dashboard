"""Tests for CLI arg building, env var building, and stage parsing."""

from __future__ import annotations

import pytest
from pipeline_app.config import EnvSecrets, PipelineAppConfig, TuningConfig
from pipeline_app.runner import (
    _base_env,
    _int_str,
    build_cli_args,
    build_env_vars,
    build_extract_config,
    parse_stage_marker,
)


class TestBuildCliArgs:
    def test_standard_mode_defaults(self):
        config = PipelineAppConfig()
        args = build_cli_args(config)
        assert args == ["pipeline/main.py", "--pubmed", "--days-back", "7"]

    def test_standard_mode_with_flags(self):
        config = PipelineAppConfig(
            days_back=30,
            dry_run=True,
            test_mode=True,
            sync_external_data=True,
        )
        args = build_cli_args(config)
        assert args == [
            "pipeline/main.py",
            "--pubmed",
            "--days-back",
            "30",
            "--dry-run",
            "--test-mode",
            "--sync-external-data",
        ]

    def test_local_pdfs_mode(self):
        config = PipelineAppConfig(run_mode="local_pdfs", local_pdfs_path="/data/pdfs")
        args = build_cli_args(config)
        assert args == [
            "pipeline/main.py",
            "--local-pdfs",
            "/data/pdfs",
        ]

    def test_local_pdfs_with_skip_validation(self):
        config = PipelineAppConfig(
            run_mode="local_pdfs",
            local_pdfs_path="/data/pdfs",
            skip_validation=True,
        )
        args = build_cli_args(config)
        assert "--skip-validation" in args

    def test_pmid_list_mode(self):
        config = PipelineAppConfig(run_mode="pmid_list", pmids_path="/data/pmids.txt")
        args = build_cli_args(config)
        assert args == [
            "pipeline/main.py",
            "--pmids",
            "/data/pmids.txt",
        ]

    def test_pmid_list_with_skip_validation(self):
        config = PipelineAppConfig(
            run_mode="pmid_list",
            pmids_path="/data/pmids.txt",
            skip_validation=True,
        )
        args = build_cli_args(config)
        assert "--skip-validation" in args


class TestBuildEnvVars:
    def test_includes_pythonunbuffered(self):
        env = build_env_vars(PipelineAppConfig(), EnvSecrets())
        assert env["PYTHONUNBUFFERED"] == "1"

    def test_includes_credentials(self):
        secrets = EnvSecrets(
            anthropic_api_key="sk-test",
            db_host="localhost",
            db_password="secret",
        )
        env = build_env_vars(PipelineAppConfig(), secrets)
        assert env["ANTHROPIC_API_KEY"] == "sk-test"
        assert env["DB_HOST"] == "localhost"
        assert env["DB_PASSWORD"] == "secret"

    def test_includes_pipeline_config_vars(self):
        config = PipelineAppConfig(
            llm_model="claude-sonnet-4-6",
            confidence_threshold=0.8,
            max_concurrent_papers=10,
        )
        env = build_env_vars(config, EnvSecrets())
        assert env["PIPELINE_LLM_MODEL"] == "claude-sonnet-4-6"
        assert env["PIPELINE_CONFIDENCE_THRESHOLD"] == "0.8"
        assert env["PIPELINE_MAX_CONCURRENT_PAPERS"] == "10"

    def test_includes_all_pipeline_vars(self):
        env = build_env_vars(PipelineAppConfig(), EnvSecrets())
        expected_keys = [
            "PIPELINE_LLM_MODEL",
            "PIPELINE_LLM_EFFORT",
            "PIPELINE_LLM_MAX_TOKENS",
            "PIPELINE_PROMPT_VERSION",
            "PIPELINE_CONFIDENCE_THRESHOLD",
            "PIPELINE_MAX_CONCURRENT_PAPERS",
            "PIPELINE_RPM_LIMIT",
            "PIPELINE_TPM_LIMIT",
            "PIPELINE_ESTIMATED_TOKENS_PER_CALL",
            "PIPELINE_NCBI_RATE_LIMIT",
            "PIPELINE_UNIPROT_RATE_LIMIT",
            "PIPELINE_MAX_PAPER_TEXT_CHARS",
            "PIPELINE_MAX_RETRIES",
            "PIPELINE_RETRY_DELAY",
            "PIPELINE_MAX_RATE_LIMIT_RETRIES",
            "PIPELINE_RATE_LIMIT_RETRY_DELAY",
            "PIPELINE_MAX_CONNECTION_RETRIES",
            "PIPELINE_CONNECTION_RETRY_DELAY",
            "PIPELINE_DB_POOL_MIN",
            "PIPELINE_DB_POOL_MAX",
            "PIPELINE_DB_COMMAND_TIMEOUT",
        ]
        for key in expected_keys:
            assert key in env, f"Missing env var: {key}"

    def test_includes_progress_file_when_set(self):
        config = PipelineAppConfig(progress_file="/tmp/progress.json")
        env = build_env_vars(config, EnvSecrets())
        assert env["PIPELINE_PROGRESS_FILE"] == "/tmp/progress.json"

    def test_excludes_progress_file_when_empty(self):
        env = build_env_vars(PipelineAppConfig(), EnvSecrets())
        assert "PIPELINE_PROGRESS_FILE" not in env

    def test_includes_system_path(self):
        env = build_env_vars(PipelineAppConfig(), EnvSecrets())
        assert "PATH" in env


class TestParseStageMarker:
    def test_parses_search_stage(self):
        assert parse_stage_marker("##STAGE:search##") == "search"

    def test_parses_extract_stage(self):
        assert parse_stage_marker("##STAGE:extract##") == "extract"

    def test_parses_all_stages(self):
        for stage in (
            "search",
            "retrieve",
            "extract",
            "validate",
            "merge",
            "sync",
        ):
            assert parse_stage_marker(f"##STAGE:{stage}##") == stage

    def test_returns_none_for_regular_line(self):
        assert parse_stage_marker("Processing paper 12345...") is None

    def test_returns_none_for_empty_line(self):
        assert parse_stage_marker("") is None

    def test_handles_trailing_whitespace(self):
        assert parse_stage_marker("##STAGE:search##  \n") == "search"


class TestBaseEnv:
    def test_forwards_home(self, monkeypatch):
        monkeypatch.setenv("HOME", "/test/home")
        env = _base_env()
        assert env["HOME"] == "/test/home"

    def test_forwards_ssl_cert_vars(self, monkeypatch):
        monkeypatch.setenv("SSL_CERT_FILE", "/etc/ssl/cert.pem")
        monkeypatch.setenv("SSL_CERT_DIR", "/etc/ssl/certs")
        env = _base_env()
        assert env["SSL_CERT_FILE"] == "/etc/ssl/cert.pem"
        assert env["SSL_CERT_DIR"] == "/etc/ssl/certs"

    def test_excludes_unrelated_vars(self, monkeypatch):
        monkeypatch.setenv("SOME_RANDOM_VAR", "value")
        env = _base_env()
        assert "SOME_RANDOM_VAR" not in env


class TestIntStr:
    """Verify _int_str handles NiceGUI float→int conversion."""

    def test_converts_float_to_int_string(self):
        assert _int_str(7.0) == "7"

    def test_preserves_int(self):
        assert _int_str(7) == "7"

    def test_truncates_fractional(self):
        assert _int_str(7.9) == "7"


class TestBuildCliArgsFloat:
    """Verify build_cli_args handles NiceGUI float values."""

    def test_days_back_as_float(self):
        config = PipelineAppConfig(days_back=30)
        config.days_back = 30.0  # ty: ignore[invalid-assignment]
        args = build_cli_args(config)
        assert "--days-back" in args
        assert args[args.index("--days-back") + 1] == "30"


class TestProviderEnvVars:
    def test_provider_env_vars_passed_to_subprocess(self):
        cfg = PipelineAppConfig(
            llm_provider="ollama",
            ollama_host="http://gpu:11434",
            ollama_model="svd-gemma:v1",
            ollama_num_ctx=131_072,
        )
        env = build_env_vars(cfg, EnvSecrets())
        assert env["PIPELINE_LLM_PROVIDER"] == "ollama"
        assert env["PIPELINE_OLLAMA_HOST"] == "http://gpu:11434"
        assert env["PIPELINE_OLLAMA_MODEL"] == "svd-gemma:v1"
        assert env["PIPELINE_OLLAMA_NUM_CTX"] == "131072"

    def test_anthropic_provider_env_vars_in_subprocess(self):
        cfg = PipelineAppConfig()  # defaults
        env = build_env_vars(cfg, EnvSecrets())
        assert env["PIPELINE_LLM_PROVIDER"] == "anthropic"
        assert env["PIPELINE_OLLAMA_HOST"] == "http://localhost:11434"
        assert env["PIPELINE_OLLAMA_MODEL"] == "gemma4:e4b"
        assert env["PIPELINE_OLLAMA_NUM_CTX"] == "65536"


class TestBuildEnvVarsFloat:
    """Verify build_env_vars handles NiceGUI float values for int fields."""

    def test_int_fields_serialized_without_decimal(self):
        config = PipelineAppConfig()
        # Simulate NiceGUI setting float values on int fields
        config.max_concurrent_papers = 10.0  # ty: ignore[invalid-assignment]
        config.rpm_limit = 50.0  # ty: ignore[invalid-assignment]
        env = build_env_vars(config, EnvSecrets())
        assert env["PIPELINE_MAX_CONCURRENT_PAPERS"] == "10"
        assert env["PIPELINE_RPM_LIMIT"] == "50"


class TestBuildCliArgsEdgeCases:
    def test_unknown_run_mode_returns_bare_args(self):
        config = PipelineAppConfig(run_mode="unknown_mode")
        args = build_cli_args(config)
        assert args == ["pipeline/main.py"]

    def test_standard_only_dry_run(self):
        config = PipelineAppConfig(dry_run=True, test_mode=False)
        args = build_cli_args(config)
        assert "--dry-run" in args
        assert "--test-mode" not in args
        assert "--sync-external-data" not in args

    def test_standard_only_test_mode(self):
        config = PipelineAppConfig(test_mode=True, dry_run=False)
        args = build_cli_args(config)
        assert "--test-mode" in args
        assert "--dry-run" not in args

    def test_standard_only_sync(self):
        config = PipelineAppConfig(sync_external_data=True)
        args = build_cli_args(config)
        assert "--sync-external-data" in args
        assert "--dry-run" not in args
        assert "--test-mode" not in args


class TestBuildExtractConfigOllamaFields:
    """Verify Ollama fields propagate from TuningConfig into build_extract_config."""

    def test_ollama_fields_propagate_when_not_use_main_config(self):
        main_config = PipelineAppConfig()
        tuning = TuningConfig(
            use_main_config=False,
            llm_provider="ollama",
            ollama_model="svd-gemma:v1",
            ollama_host="http://gpu-server:11434",
            ollama_num_ctx=131_072,
            pdf_path="/data/pdfs",
        )
        result = build_extract_config(main_config, tuning)
        assert result.llm_provider == "ollama"
        assert result.ollama_model == "svd-gemma:v1"
        assert result.ollama_host == "http://gpu-server:11434"
        assert result.ollama_num_ctx == 131_072

    def test_ollama_fields_not_overridden_when_use_main_config(self):
        # When use_main_config=True, main_config's provider settings are kept.
        main_config = PipelineAppConfig(
            llm_provider="anthropic",
            ollama_model="gemma4:e4b",
        )
        tuning = TuningConfig(
            use_main_config=True,
            llm_provider="ollama",
            ollama_model="svd-gemma:v1",
            pdf_path="/data/pdfs",
        )
        result = build_extract_config(main_config, tuning)
        # main_config values survive because use_main_config=True skips the
        # override block.
        assert result.llm_provider == "anthropic"
        assert result.ollama_model == "gemma4:e4b"

    def test_anthropic_provider_propagates_when_not_use_main_config(self):
        main_config = PipelineAppConfig(llm_provider="ollama")
        tuning = TuningConfig(
            use_main_config=False,
            llm_provider="anthropic",
            llm_model="claude-sonnet-4-6",
            pdf_path="/data/pdfs",
        )
        result = build_extract_config(main_config, tuning)
        assert result.llm_provider == "anthropic"
        assert result.llm_model == "claude-sonnet-4-6"


class TestBuildCliArgsDaysBackValidation:
    """Regression tests: standard mode rejects non-positive days_back."""

    def test_zero_raises(self):
        config = PipelineAppConfig(days_back=0)
        with pytest.raises(ValueError, match="days_back must be >= 1"):
            build_cli_args(config)

    def test_negative_raises(self):
        config = PipelineAppConfig(days_back=-5)
        with pytest.raises(ValueError, match="days_back must be >= 1"):
            build_cli_args(config)

    def test_one_is_valid(self):
        config = PipelineAppConfig(days_back=1)
        args = build_cli_args(config)
        assert args[args.index("--days-back") + 1] == "1"

    def test_negative_only_blocks_standard_mode(self):
        # local_pdfs mode doesn't use days_back, so the validation must
        # not fire there even with a bad value.
        config = PipelineAppConfig(
            run_mode="local_pdfs",
            local_pdfs_path="/data/p",
            days_back=-1,
        )
        args = build_cli_args(config)  # Should not raise
        assert "--local-pdfs" in args
