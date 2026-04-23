"""Tests for the shared types in pipeline.llm_providers.base."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from pipeline.llm_providers.base import (
    ExtractionResult,
    GeneEntry,
    LLMProvider,
    parse_extraction_response,
)


class TestGeneEntry:
    def test_minimal_valid_entry(self):
        g = GeneEntry(gene_symbol="NOTCH3", confidence=0.9)
        assert g.gene_symbol == "NOTCH3"
        assert g.confidence == 0.9
        assert g.gwas_trait == []
        assert g.mendelian_randomization is False
        assert g.pmid == ""

    def test_confidence_out_of_range_rejected(self):
        with pytest.raises(ValidationError):
            GeneEntry(gene_symbol="X", confidence=1.5)
        with pytest.raises(ValidationError):
            GeneEntry(gene_symbol="X", confidence=-0.1)

    def test_whitespace_stripped(self):
        g = GeneEntry(gene_symbol="  NOTCH3  ", confidence=0.9)
        assert g.gene_symbol == "NOTCH3"


class TestExtractionResult:
    def test_empty(self):
        r = ExtractionResult()
        assert r.genes == []

    def test_roundtrip(self):
        payload = {"genes": [{"gene_symbol": "NOTCH3", "confidence": 0.85}]}
        r = ExtractionResult.model_validate(payload)
        assert r.genes[0].gene_symbol == "NOTCH3"


class TestParseExtractionResponse:
    def test_valid_json(self):
        raw = json.dumps({"genes": [{"gene_symbol": "TREX1", "confidence": 0.7}]})
        r = parse_extraction_response(raw)
        assert len(r.genes) == 1

    def test_invalid_confidence_raises(self):
        raw = json.dumps({"genes": [{"gene_symbol": "X", "confidence": 2.0}]})
        with pytest.raises(ValidationError):
            parse_extraction_response(raw)

    def test_malformed_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            parse_extraction_response("not json")


class TestLLMProviderProtocol:
    def test_protocol_is_runtime_checkable(self):
        class Stub:
            name = "stub"
            async def extract(self, text, pmid, config, rate_limiter):
                return [], None
            async def close(self):
                pass
            def supports_thinking(self):
                return False
            def supports_prompt_caching(self):
                return False
            def report_metadata(self, config):
                return {}
            def estimate_cost(self, usage, config):
                return None

        assert isinstance(Stub(), LLMProvider)
