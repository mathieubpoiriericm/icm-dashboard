"""End-to-end Ollama-provider dispatch test with the SDK boundary mocked."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import ollama
import pytest

from pipeline.config import PipelineConfig
from pipeline.llm_extraction import close_async_client, extract_from_paper

SAMPLE_TEXT = (
    "Genome-wide association studies have identified NOTCH3 as a monogenic "
    "cause of CADASIL, a prototypical cerebral small vessel disease. Loss-of-"
    "function variants in HTRA1 cause CARASIL. A recent MTAG analysis of WMH "
    "and lacunar stroke identified FOXF2 at 6p25 with high confidence."
)


def _chat_response(content: str) -> ollama.ChatResponse:
    """Build a realistic ChatResponse for the mocked Ollama SDK."""
    return ollama.ChatResponse(
        model="gemma4:e4b",
        message=ollama.Message(role="assistant", content=content),
        eval_count=75,
        prompt_eval_count=250,
        done_reason="stop",
    )


@pytest.mark.asyncio
async def test_ollama_end_to_end(monkeypatch):
    list_response = MagicMock()
    list_response.models = [MagicMock(model="gemma4:e4b")]

    mock_client = MagicMock()
    mock_client.list = AsyncMock(return_value=list_response)
    mock_client.chat = AsyncMock(
        return_value=_chat_response(
            """
            {
              "genes": [
                {
                  "gene_symbol": "NOTCH3",
                  "confidence": 0.92,
                  "protein_name": "Notch receptor 3",
                  "gwas_trait": ["SVS"],
                  "mendelian_randomization": false,
                  "omics_evidence": []
                },
                {
                  "gene_symbol": "HTRA1",
                  "confidence": 0.88,
                  "protein_name": "HtrA serine peptidase 1",
                  "gwas_trait": ["WMH"],
                  "mendelian_randomization": false,
                  "omics_evidence": []
                }
              ]
            }
            """
        )
    )
    monkeypatch.setattr(ollama, "AsyncClient", lambda host=None: mock_client)
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
        mock_client.list.assert_awaited_once()
        mock_client.chat.assert_awaited_once()
    finally:
        await close_async_client()
