"""Tests for pipeline.config — defaults, env-var overrides, constants."""

from __future__ import annotations

from pipeline.config import (
    ALLOWED_COLUMNS,
    ALLOWED_TABLES,
    PROJECT_ROOT,
    VALID_GWAS_TRAITS,
    PipelineConfig,
)


class TestPipelineConfigDefaults:
    """Verify default values are sensible and stable."""

    def test_default_model(self):
        cfg = PipelineConfig()
        assert cfg.llm_model == "claude-opus-4-6"

    def test_default_max_tokens(self):
        cfg = PipelineConfig()
        assert cfg.llm_max_tokens == 64_000

    def test_default_effort(self):
        cfg = PipelineConfig()
        assert cfg.llm_effort == "high"

    def test_default_max_paper_text_chars(self):
        cfg = PipelineConfig()
        assert cfg.max_paper_text_chars == 100_000

    def test_default_max_retries(self):
        cfg = PipelineConfig()
        assert cfg.max_retries == 1

    def test_default_retry_delay(self):
        cfg = PipelineConfig()
        assert cfg.retry_delay == 2.0

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
        monkeypatch.setenv("PIPELINE_LLM_MODEL", "claude-sonnet-4-5-20250929")
        cfg = PipelineConfig()
        assert cfg.llm_model == "claude-sonnet-4-5-20250929"

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
