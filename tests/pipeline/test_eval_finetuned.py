"""Unit tests for scripts.finetune.eval_finetuned helpers."""

from scripts.finetune.eval_finetuned import (
    EvalConfig,
    build_configs,
    parse_report_arg,
)


class TestEvalConfig:
    def test_is_dataclass_with_expected_fields(self):
        c = EvalConfig(label="test", llm_provider="anthropic")
        assert c.label == "test"
        assert c.llm_provider == "anthropic"
        assert c.llm_model is None
        assert c.ollama_model is None
        assert c.prompt_version == "v5"


class TestBuildConfigs:
    def test_returns_three(self):
        configs = build_configs(finetuned_tag="svd-gemma:v1")
        assert len(configs) == 3

    def test_labels_and_providers(self):
        configs = build_configs(finetuned_tag="svd-gemma:v1")
        labels = [c.label for c in configs]
        assert labels == ["claude-baseline", "gemma4-base", "svd-gemma:v1"]
        providers = [c.llm_provider for c in configs]
        assert providers == ["anthropic", "ollama", "ollama"]

    def test_claude_config(self):
        c = build_configs("svd-gemma:v1")[0]
        assert c.llm_model == "claude-opus-4-7"
        assert c.prompt_version == "v5"

    def test_gemma_base_config(self):
        c = build_configs("svd-gemma:v1")[1]
        assert c.ollama_model == "gemma4:e4b"
        assert c.prompt_version == "ollama_v1"

    def test_finetuned_config_uses_custom_tag(self):
        c = build_configs("svd-gemma:v7")[2]
        assert c.ollama_model == "svd-gemma:v7"
        assert c.prompt_version == "ollama_v1"


class TestParseReportArg:
    def test_basic_colon_split(self):
        label, path = parse_report_arg("claude-baseline:logs/report.json")
        assert label == "claude-baseline"
        assert str(path) == "logs/report.json"

    def test_label_with_colon_in_tag(self):
        """A label like `svd-gemma:v1` has its own colon. Ensure we split on
        the LAST colon, not the first."""
        label, path = parse_report_arg("svd-gemma:v1:logs/report.json")
        assert label == "svd-gemma:v1"
        assert str(path) == "logs/report.json"

    def test_missing_colon_raises(self):
        import pytest

        with pytest.raises(ValueError):
            parse_report_arg("no_colon_here")
