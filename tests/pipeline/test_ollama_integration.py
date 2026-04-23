"""End-to-end test against a real local Ollama server.

Skipped unless PIPELINE_TEST_OLLAMA=1. Requires:
  - `ollama serve` running
  - `ollama pull gemma4:e4b` completed
"""

from __future__ import annotations

import os

import pytest

from pipeline.config import PipelineConfig
from pipeline.llm_extraction import close_async_client, extract_from_paper

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("PIPELINE_TEST_OLLAMA") != "1",
        reason="set PIPELINE_TEST_OLLAMA=1 to run",
    ),
]


SAMPLE_TEXT = (
    "Genome-wide association studies have identified NOTCH3 as a monogenic "
    "cause of CADASIL, a prototypical cerebral small vessel disease. Loss-of-"
    "function variants in HTRA1 cause CARASIL. A recent MTAG analysis of WMH "
    "and lacunar stroke identified FOXF2 at 6p25 with high confidence."
)


@pytest.mark.asyncio
async def test_ollama_end_to_end(monkeypatch):
    # Force the ollama provider for this test regardless of env defaults.
    monkeypatch.setenv("PIPELINE_LLM_PROVIDER", "ollama")
    monkeypatch.delenv("PIPELINE_PROMPT_VERSION", raising=False)
    try:
        cfg = PipelineConfig()
        assert cfg.llm_provider == "ollama"
        assert cfg.prompt_version == "ollama_v1"  # auto-switched

        genes, usage = await extract_from_paper(SAMPLE_TEXT, "99999999", cfg, None)

        assert isinstance(genes, list)
        # The text names NOTCH3, HTRA1, FOXF2 — expect at least one.
        symbols = {g.gene_symbol for g in genes}
        assert symbols & {"NOTCH3", "HTRA1", "FOXF2"}, (
            f"Expected at least one of NOTCH3/HTRA1/FOXF2; got {symbols}"
        )
        for g in genes:
            assert g.pmid == "99999999"
            assert 0.0 <= g.confidence <= 1.0
        assert usage.output_tokens > 0
    finally:
        await close_async_client()
