"""Tests for pipeline.llm_extraction — shared types and dispatcher interface.

Claude-specific streaming / retry / thinking tests live in
tests/pipeline/test_anthropic_provider.py.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from pipeline.llm_extraction import GeneEntry, extract_from_paper
from pipeline.llm_providers.base import ExtractionResult, parse_extraction_response

# ---------------------------------------------------------------------------
# GeneEntry Pydantic model
# ---------------------------------------------------------------------------


class TestGeneEntryModel:
    def test_minimal_valid(self):
        ge = GeneEntry(gene_symbol="NOTCH3", confidence=0.9)
        assert ge.gene_symbol == "NOTCH3"
        assert ge.confidence == 0.9
        assert ge.protein_name is None
        assert ge.gwas_trait == []
        assert ge.omics_evidence == []
        assert ge.mendelian_randomization is False
        assert ge.pmid == ""

    def test_full_fields(self):
        ge = GeneEntry(
            gene_symbol="HTRA1",
            protein_name="Serine protease HTRA1",
            gwas_trait=["WMH"],
            mendelian_randomization=True,
            omics_evidence=["TWAS"],
            confidence=0.95,
            causal_evidence_summary="Strong evidence",
            pmid="12345678",
        )
        assert ge.gene_symbol == "HTRA1"
        assert ge.mendelian_randomization is True

    def test_confidence_lower_bound(self):
        ge = GeneEntry(gene_symbol="X", confidence=0.0)
        assert ge.confidence == 0.0

    def test_confidence_upper_bound(self):
        ge = GeneEntry(gene_symbol="X", confidence=1.0)
        assert ge.confidence == 1.0

    def test_confidence_below_zero_rejected(self):
        with pytest.raises(ValidationError):
            GeneEntry(gene_symbol="X", confidence=-0.1)

    def test_confidence_above_one_rejected(self):
        with pytest.raises(ValidationError):
            GeneEntry(gene_symbol="X", confidence=1.1)

    def test_whitespace_stripped(self):
        ge = GeneEntry(gene_symbol="  NOTCH3  ", confidence=0.9)
        assert ge.gene_symbol == "NOTCH3"

    def test_missing_gene_symbol_rejected(self):
        with pytest.raises(ValidationError):
            GeneEntry(confidence=0.9)  # ty: ignore[missing-argument]

    def test_missing_confidence_rejected(self):
        with pytest.raises(ValidationError):
            GeneEntry(gene_symbol="X")  # ty: ignore[missing-argument]

    def test_pmid_mutable(self):
        ge = GeneEntry(gene_symbol="X", confidence=0.9)
        ge.pmid = "99999999"
        assert ge.pmid == "99999999"


class TestExtractionResult:
    def test_empty_genes(self):
        er = ExtractionResult(genes=[])
        assert er.genes == []

    def test_default_empty(self):
        er = ExtractionResult()
        assert er.genes == []

    def test_with_genes(self):
        er = ExtractionResult(genes=[GeneEntry(gene_symbol="X", confidence=0.9)])
        assert len(er.genes) == 1

    def test_model_json_schema(self):
        schema = ExtractionResult.model_json_schema()
        assert "properties" in schema
        assert "genes" in schema["properties"]


# ---------------------------------------------------------------------------
# parse_extraction_response
# ---------------------------------------------------------------------------


class TestParseExtractionResponse:
    def test_clean_json(self):
        text = '{"genes": [{"gene_symbol": "NOTCH3", "confidence": 0.9}]}'
        result = parse_extraction_response(text)
        assert len(result.genes) == 1
        assert result.genes[0].gene_symbol == "NOTCH3"

    def test_empty_genes(self):
        result = parse_extraction_response('{"genes": []}')
        assert result.genes == []

    def test_no_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            parse_extraction_response("no json here at all")

    def test_invalid_schema_returns_empty(self):
        # ExtractionResult has a default empty genes list, so unknown keys
        # don't raise — they just produce an empty result.
        result = parse_extraction_response('{"not_genes": []}')
        assert result.genes == []

    def test_whitespace_handling(self):
        text = '  \n  {"genes": []}  \n  '
        result = parse_extraction_response(text)
        assert result.genes == []

    def test_multiple_genes(self):
        data = {
            "genes": [
                {"gene_symbol": "A", "confidence": 0.9},
                {"gene_symbol": "B", "confidence": 0.8},
                {"gene_symbol": "C", "confidence": 0.7},
            ]
        }
        result = parse_extraction_response(json.dumps(data))
        assert len(result.genes) == 3

    def test_confidence_out_of_range_raises(self):
        text = '{"genes": [{"gene_symbol": "X", "confidence": 1.5}]}'
        with pytest.raises(ValidationError):
            parse_extraction_response(text)


# ---------------------------------------------------------------------------
# extract_from_paper — dispatcher-level interface tests (no LLM call)
# ---------------------------------------------------------------------------


class TestExtractFromPaperDispatcher:
    async def test_empty_text_returns_empty(self):
        genes, usage = await extract_from_paper("", "12345678")
        assert genes == []
        assert usage.total_tokens == 0

    async def test_whitespace_text_returns_empty(self):
        genes, usage = await extract_from_paper("   \n  ", "12345678")
        assert genes == []
