"""Tests for pipeline.config — defaults, env-var overrides, constants."""

from __future__ import annotations

import pytest

from pipeline.config import (
    ALLOWED_COLUMNS,
    ALLOWED_TABLES,
    LLM_PROVIDERS,
    MODEL_MAX_OUTPUT_TOKENS,
    PROJECT_ROOT,
    VALID_GWAS_TRAITS,
    PipelineConfig,
    validate_pmid,
)


class TestPipelineConfigDefaults:
    """Verify default values are sensible and stable."""

    def test_default_model(self):
        cfg = PipelineConfig()
        assert cfg.llm_model == "claude-opus-4-7"

    def test_default_max_tokens_matches_model(self):
        cfg = PipelineConfig()
        assert cfg.llm_max_tokens == MODEL_MAX_OUTPUT_TOKENS[cfg.llm_model]

    def test_default_effort(self):
        cfg = PipelineConfig()
        assert cfg.llm_effort == "high"

    def test_default_max_paper_text_chars(self):
        cfg = PipelineConfig()
        assert cfg.max_paper_text_chars == 100_000

    def test_default_max_retries(self):
        cfg = PipelineConfig()
        assert cfg.max_retries == 1

    def test_default_confidence_threshold(self):
        cfg = PipelineConfig()
        assert cfg.confidence_threshold == 0.65

    def test_default_rpm_limit(self):
        cfg = PipelineConfig()
        assert cfg.rpm_limit == 50

    def test_default_tpm_limit(self):
        cfg = PipelineConfig()
        assert cfg.tpm_limit == 100_000

    def test_default_db_pool_sizes(self):
        cfg = PipelineConfig()
        assert cfg.db_pool_min_size == 2
        assert cfg.db_pool_max_size == 10

    def test_days_back_range(self):
        cfg = PipelineConfig()
        assert cfg.min_days_back == 1
        assert cfg.max_days_back == 3650


class TestPipelineConfigEnvOverrides:
    """Verify env-var overrides via monkeypatch."""

    def test_override_llm_model(self, monkeypatch):
        monkeypatch.setenv("PIPELINE_LLM_MODEL", "claude-sonnet-4-6")
        cfg = PipelineConfig()
        assert cfg.llm_model == "claude-sonnet-4-6"

    def test_override_max_tokens(self, monkeypatch):
        monkeypatch.setenv("PIPELINE_LLM_MAX_TOKENS", "16000")
        cfg = PipelineConfig()
        assert cfg.llm_max_tokens == 16_000

    def test_override_confidence_threshold(self, monkeypatch):
        monkeypatch.setenv("PIPELINE_CONFIDENCE_THRESHOLD", "0.85")
        cfg = PipelineConfig()
        assert cfg.confidence_threshold == 0.85

    def test_override_rpm(self, monkeypatch):
        monkeypatch.setenv("PIPELINE_RPM_LIMIT", "100")
        cfg = PipelineConfig()
        assert cfg.rpm_limit == 100

    def test_override_effort(self, monkeypatch):
        monkeypatch.setenv("PIPELINE_LLM_EFFORT", "low")
        cfg = PipelineConfig()
        assert cfg.llm_effort == "low"


class TestConstants:
    """Verify module-level constants are valid."""

    def test_valid_gwas_traits_nonempty(self):
        assert len(VALID_GWAS_TRAITS) > 0

    def test_known_traits_present(self):
        for trait in ("WMH", "SVS", "lacunes", "stroke"):
            assert trait in VALID_GWAS_TRAITS

    def test_allowed_tables(self):
        assert "genes" in ALLOWED_TABLES
        assert "pubmed_refs" in ALLOWED_TABLES

    def test_allowed_columns(self):
        assert "id" in ALLOWED_COLUMNS

    def test_project_root_exists(self):
        assert PROJECT_ROOT.exists()
        assert (PROJECT_ROOT / "pipeline").is_dir()


class TestValidatePmid:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("12345678", "12345678"),
            ("1", "1"),
            ("  12345678  ", "12345678"),
            ("123456789", "123456789"),
        ],
    )
    def test_accepts_valid(self, raw, expected):
        assert validate_pmid(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        ["abc123", "", "1234567890", "1234-5678"],
    )
    def test_rejects_invalid(self, raw):
        with pytest.raises(ValueError, match="Invalid PMID"):
            validate_pmid(raw)


class TestProviderFields:
    """Verify provider-selection fields and LLM_PROVIDERS constant."""

    # Restore env vars after each test so other tests are not affected.
    @pytest.fixture(autouse=True)
    def restore_env(self, monkeypatch):
        yield
        # monkeypatch automatically undoes setenv/delenv after each test.

    def test_default_provider_is_anthropic(self, monkeypatch):
        monkeypatch.delenv("PIPELINE_LLM_PROVIDER", raising=False)
        cfg = PipelineConfig()
        assert cfg.llm_provider == "anthropic"

    def test_ollama_provider_env_override(self, monkeypatch):
        monkeypatch.setenv("PIPELINE_LLM_PROVIDER", "ollama")
        cfg = PipelineConfig()
        assert cfg.llm_provider == "ollama"

    def test_ollama_defaults(self, monkeypatch):
        monkeypatch.delenv("PIPELINE_OLLAMA_HOST", raising=False)
        monkeypatch.delenv("PIPELINE_OLLAMA_MODEL", raising=False)
        monkeypatch.delenv("PIPELINE_OLLAMA_NUM_CTX", raising=False)
        cfg = PipelineConfig()
        assert cfg.ollama_host == "http://localhost:11434"
        assert cfg.ollama_model == "gemma4:e4b"
        assert cfg.ollama_num_ctx == 65536

    def test_ollama_env_overrides(self, monkeypatch):
        monkeypatch.setenv("PIPELINE_OLLAMA_HOST", "http://gpu-box:11434")
        monkeypatch.setenv("PIPELINE_OLLAMA_MODEL", "svd-gemma:v1")
        monkeypatch.setenv("PIPELINE_OLLAMA_NUM_CTX", "131072")
        cfg = PipelineConfig()
        assert cfg.ollama_host == "http://gpu-box:11434"
        assert cfg.ollama_model == "svd-gemma:v1"
        assert cfg.ollama_num_ctx == 131072

    def test_unknown_provider_rejected_in_post_init(self, monkeypatch):
        monkeypatch.setenv("PIPELINE_LLM_PROVIDER", "bogus")
        with pytest.raises(ValueError, match="llm_provider"):
            PipelineConfig()

    def test_llm_providers_constant(self):
        assert set(LLM_PROVIDERS) == {"anthropic", "ollama"}

    def test_ollama_auto_switches_prompt_version(self, monkeypatch):
        monkeypatch.setenv("PIPELINE_LLM_PROVIDER", "ollama")
        monkeypatch.delenv("PIPELINE_PROMPT_VERSION", raising=False)
        cfg = PipelineConfig()
        assert cfg.prompt_version == "ollama_v1"

    def test_ollama_explicit_prompt_version_preserved(self, monkeypatch):
        monkeypatch.setenv("PIPELINE_LLM_PROVIDER", "ollama")
        monkeypatch.setenv("PIPELINE_PROMPT_VERSION", "v5")
        cfg = PipelineConfig()
        assert cfg.prompt_version == "v5"

    def test_anthropic_does_not_touch_prompt_version(self, monkeypatch):
        monkeypatch.delenv("PIPELINE_LLM_PROVIDER", raising=False)
        monkeypatch.delenv("PIPELINE_PROMPT_VERSION", raising=False)
        cfg = PipelineConfig()
        assert cfg.prompt_version == "v5"

    def test_anthropic_config_does_not_require_api_key(self, monkeypatch):
        """Non-LLM pipeline modes can still build config without Anthropic creds."""
        monkeypatch.setenv("PIPELINE_LLM_PROVIDER", "anthropic")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        cfg = PipelineConfig()
        assert cfg.llm_provider == "anthropic"
