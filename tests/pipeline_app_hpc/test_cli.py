"""Tests for pipeline_app_hpc.cli."""

from __future__ import annotations

import pytest


class TestCliArgs:
    def test_build_parser_has_local_pdfs(self):
        from pipeline_app_hpc.cli import _build_parser

        p = _build_parser()
        ns = p.parse_args(["--local-pdfs", "/tmp/x"])
        assert str(ns.local_pdfs) == "/tmp/x"
        assert ns.skip_validation is False

    def test_build_parser_skip_validation(self):
        from pipeline_app_hpc.cli import _build_parser

        p = _build_parser()
        ns = p.parse_args(["--local-pdfs", "/tmp/x", "--skip-validation"])
        assert ns.skip_validation is True


class TestBuildConfig:
    def test_reads_pipeline_env(self, monkeypatch):
        from pipeline_app_hpc.cli import build_pipeline_config

        monkeypatch.setenv("PIPELINE_CONFIDENCE_THRESHOLD", "0.42")
        monkeypatch.setenv("PIPELINE_MAX_PAPER_TEXT_CHARS", "12345")
        cfg = build_pipeline_config()
        assert cfg.confidence_threshold == 0.42
        assert cfg.max_paper_text_chars == 12345


class TestBuildProvider:
    def test_uses_env_vars(self, monkeypatch):
        from pipeline_app_hpc.cli import build_provider

        monkeypatch.setenv("VLLM_BASE_URL", "http://127.0.0.1:31000")
        monkeypatch.setenv("VLLM_MODEL", "svd")
        monkeypatch.setenv("VLLM_BASE_MODEL_NAME", "unsloth/gemma-4-31b")
        monkeypatch.setenv("VLLM_ADAPTER_NAME", "svd")
        provider = build_provider()
        assert provider.name == "vllm"
        assert provider._model == "svd"
        assert provider._base_url == "http://127.0.0.1:31000"

    def test_missing_base_url_raises(self, monkeypatch):
        from pipeline_app_hpc.cli import build_provider

        monkeypatch.delenv("VLLM_BASE_URL", raising=False)
        with pytest.raises(SystemExit, match="VLLM_BASE_URL"):
            build_provider()
