"""Tests for pipeline_app_hpc.extract orchestration loop."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


class _StubProvider:
    """Minimal stub matching the LLMProvider protocol."""

    name = "vllm-stub"

    def __init__(self, genes_per_pdf: int = 1):
        self._genes_per_pdf = genes_per_pdf

    async def extract(self, text, pmid, config, rate_limiter):
        from pipeline.llm_providers.base import GeneEntry
        from pipeline.quality_metrics import TokenUsage

        return (
            [
                GeneEntry(gene_symbol=f"G_{pmid}", confidence=0.9, pmid=pmid)
                for _ in range(self._genes_per_pdf)
            ],
            TokenUsage(input_tokens=10, output_tokens=5),
        )

    async def close(self):
        pass

    def supports_thinking(self):
        return False

    def supports_prompt_caching(self):
        return False

    def report_metadata(self, config):
        return {
            "model": "stub",
            "model_version": "",
            "thinking_mode": "none",
            "effort": None,
            "prompt_version": config.prompt_version,
            "vllm_adapter": None,
            "vllm_max_model_len": 0,
            "vllm_quantization": "",
        }

    def estimate_cost(self, usage, config):
        return None


class TestExtractRun:
    @pytest.mark.asyncio
    async def test_writes_report_for_one_pdf(self, tmp_path: Path, mocker):
        from pipeline_app_hpc.extract import run

        from pipeline.config import PipelineConfig

        # Mock pdf parsing — no real PDF needed
        mocker.patch(
            "pipeline_app_hpc.extract.parse_local_pdf",
            return_value="paper text content",
        )
        pdf = tmp_path / "PMID12345.pdf"
        pdf.write_bytes(b"%PDF-stub")

        cfg = PipelineConfig()
        report_path = await run(
            provider=_StubProvider(genes_per_pdf=2),
            pdf_dir=tmp_path,
            config=cfg,
            skip_validation=True,
            log_dir=tmp_path / "logs",
        )

        assert report_path.is_file()
        report = json.loads(report_path.read_text())
        # The pipeline writes per-paper detail under "papers_detail"; the
        # earlier `len(report) > 0` was a tautology — any non-empty dict
        # would pass. Pin the actual contract instead.
        assert isinstance(report, dict)
        assert "papers_detail" in report
        assert isinstance(report["papers_detail"], list)
        assert len(report["papers_detail"]) == 1

    @pytest.mark.asyncio
    async def test_emits_stage_markers(self, tmp_path: Path, capsys, mocker):
        from pipeline_app_hpc.extract import run

        from pipeline.config import PipelineConfig

        mocker.patch(
            "pipeline_app_hpc.extract.parse_local_pdf",
            return_value="text",
        )
        pdf = tmp_path / "PMID1.pdf"
        pdf.write_bytes(b"%PDF-stub")
        cfg = PipelineConfig()
        await run(
            provider=_StubProvider(),
            pdf_dir=tmp_path,
            config=cfg,
            skip_validation=True,
            log_dir=tmp_path / "logs",
        )
        out = capsys.readouterr().out
        # Per-PDF parse/extract/validate phases interleave inside the
        # TaskGroup; the UI tracker runs at run granularity so they're
        # collapsed into a single 'extract' marker.
        for marker in (
            "##STAGE:extract##",
            "##STAGE:batch_validate##",
            "##STAGE:report##",
        ):
            assert marker in out
        assert "##STAGE:parse##" not in out
        assert "##STAGE:validate##" not in out

    @pytest.mark.asyncio
    async def test_one_pdf_parse_error_does_not_cancel_batch(
        self, tmp_path: Path, mocker
    ):
        from pipeline_app_hpc.extract import run

        from pipeline.config import PipelineConfig

        def parse(path: Path) -> str:
            if path.name == "bad.pdf":
                raise RuntimeError("parse exploded")
            return "paper text content"

        mocker.patch("pipeline_app_hpc.extract.parse_local_pdf", side_effect=parse)
        (tmp_path / "good.pdf").write_bytes(b"%PDF-stub")
        (tmp_path / "bad.pdf").write_bytes(b"%PDF-stub")

        report_path = await run(
            provider=_StubProvider(),
            pdf_dir=tmp_path,
            config=PipelineConfig(),
            skip_validation=True,
            log_dir=tmp_path / "logs",
        )

        report = json.loads(report_path.read_text())
        by_id = {p["pmid"]: p for p in report["papers_detail"]}
        assert by_id["good"]["error"] is None
        assert by_id["bad"]["error"] == "parse exploded"
        assert report["papers"]["processed"] == 1
        assert report["papers"]["failed"] == 1
        assert report["papers"]["total"] == 2
