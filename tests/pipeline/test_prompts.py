"""Tests for pipeline.prompts — message structure and content checks."""

from __future__ import annotations

import json

from pipeline.llm_providers.base import ExtractionResult
from pipeline.prompts import (
    _PROMPTS,
    build_extraction_prompt,
)

DEFAULT_SYSTEM_PROMPT, DEFAULT_EXTRACTION_INSTRUCTIONS = _PROMPTS["v5"]


class TestSystemPrompt:
    def test_contains_csvd(self):
        assert "cSVD" in DEFAULT_SYSTEM_PROMPT

    def test_contains_role(self):
        assert "systematic reviewer" in DEFAULT_SYSTEM_PROMPT

    def test_mentions_causal_distinction(self):
        assert "causal" in DEFAULT_SYSTEM_PROMPT
        assert "association" in DEFAULT_SYSTEM_PROMPT


class TestExtractionInstructions:
    def test_contains_task_xml(self):
        assert "<task>" in DEFAULT_EXTRACTION_INSTRUCTIONS

    def test_contains_confidence_scoring_xml(self):
        assert "<confidence_scoring>" in DEFAULT_EXTRACTION_INSTRUCTIONS

    def test_mentions_gwas(self):
        assert "GWAS" in DEFAULT_EXTRACTION_INSTRUCTIONS

    def test_mentions_mendelian_randomization(self):
        assert "Mendelian randomization" in DEFAULT_EXTRACTION_INSTRUCTIONS

    def test_mentions_omics(self):
        assert "TWAS" in DEFAULT_EXTRACTION_INSTRUCTIONS
        assert "PWAS" in DEFAULT_EXTRACTION_INSTRUCTIONS
        assert "EWAS" in DEFAULT_EXTRACTION_INSTRUCTIONS

    def test_has_xml_structure(self):
        assert "<instructions>" in DEFAULT_EXTRACTION_INSTRUCTIONS
        assert "</instructions>" in DEFAULT_EXTRACTION_INSTRUCTIONS
        assert "<inclusion_criteria>" in DEFAULT_EXTRACTION_INSTRUCTIONS
        assert "<extraction_strategy>" in DEFAULT_EXTRACTION_INSTRUCTIONS
        assert "<field_guidance>" in DEFAULT_EXTRACTION_INSTRUCTIONS

    def test_has_examples(self):
        assert "<examples>" in DEFAULT_EXTRACTION_INSTRUCTIONS
        assert 'type="include_validated"' in DEFAULT_EXTRACTION_INSTRUCTIONS
        assert 'type="include_high_confidence"' in DEFAULT_EXTRACTION_INSTRUCTIONS
        assert 'type="exclude_general_stroke"' in DEFAULT_EXTRACTION_INSTRUCTIONS
        assert 'type="exclude_pathway_only"' in DEFAULT_EXTRACTION_INSTRUCTIONS
        assert 'type="exclude_background_monogenic"' in DEFAULT_EXTRACTION_INSTRUCTIONS

    def test_has_positional_candidate_exclusion_example(self):
        assert 'type="exclude_positional_candidate"' in DEFAULT_EXTRACTION_INSTRUCTIONS

    def test_has_orf_gene_example(self):
        assert 'type="include_orf_gene"' in DEFAULT_EXTRACTION_INSTRUCTIONS

    def test_positional_candidate_warning(self):
        assert "positional candidate" in DEFAULT_EXTRACTION_INSTRUCTIONS

    def test_confidence_hard_cap(self):
        assert "maximum score is 0.30" in DEFAULT_EXTRACTION_INSTRUCTIONS
        assert "hard cap of 0.20" in DEFAULT_EXTRACTION_INSTRUCTIONS

    def test_gwas_trait_vocabulary(self):
        """GWAS traits in prompt should use canonical abbreviations."""
        assert "WMH" in DEFAULT_EXTRACTION_INSTRUCTIONS
        assert "DWMH" in DEFAULT_EXTRACTION_INSTRUCTIONS
        assert "PVWMH" in DEFAULT_EXTRACTION_INSTRUCTIONS
        assert "SVS" in DEFAULT_EXTRACTION_INSTRUCTIONS
        assert "BG-PVS" in DEFAULT_EXTRACTION_INSTRUCTIONS
        assert "WM-PVS" in DEFAULT_EXTRACTION_INSTRUCTIONS
        assert "HIP-PVS" in DEFAULT_EXTRACTION_INSTRUCTIONS
        assert "PSMD" in DEFAULT_EXTRACTION_INSTRUCTIONS
        assert "MD" in DEFAULT_EXTRACTION_INSTRUCTIONS
        assert "extreme-cSVD" in DEFAULT_EXTRACTION_INSTRUCTIONS
        assert "FA" in DEFAULT_EXTRACTION_INSTRUCTIONS
        assert "ICH-lobar" in DEFAULT_EXTRACTION_INSTRUCTIONS
        assert "ICH-non-lobar" in DEFAULT_EXTRACTION_INSTRUCTIONS
        assert "DTI-ALPS" in DEFAULT_EXTRACTION_INSTRUCTIONS
        assert "ICVF" in DEFAULT_EXTRACTION_INSTRUCTIONS
        assert "ISOVF" in DEFAULT_EXTRACTION_INSTRUCTIONS
        assert "WMH-cortical-atrophy" in DEFAULT_EXTRACTION_INSTRUCTIONS
        assert "WM-BAG" in DEFAULT_EXTRACTION_INSTRUCTIONS
        assert "retinal-vessels" in DEFAULT_EXTRACTION_INSTRUCTIONS

    def test_grounding_instruction(self):
        """Should instruct the model to verify evidence before extracting."""
        assert "Identify all passages" in DEFAULT_EXTRACTION_INSTRUCTIONS
        assert "Verify" in DEFAULT_EXTRACTION_INSTRUCTIONS


class TestBuildExtractionPrompt:
    def test_returns_extraction_prompt(self):
        prompt = build_extraction_prompt(
            paper_text="Test paper", pmid="12345678", max_chars=50000
        )
        assert isinstance(prompt.system_prompt, str)
        assert isinstance(prompt.extraction_instructions, str)
        assert isinstance(prompt.user_text, str)

    def test_parts_match_canonical_constants(self):
        prompt = build_extraction_prompt(paper_text="Test", pmid="111", max_chars=50000)
        assert prompt.system_prompt == DEFAULT_SYSTEM_PROMPT
        assert prompt.extraction_instructions == DEFAULT_EXTRACTION_INSTRUCTIONS
        assert "<instructions>" in prompt.extraction_instructions

    def test_user_text_contains_document(self):
        prompt = build_extraction_prompt(paper_text="Test", pmid="111", max_chars=50000)
        assert "<document" in prompt.user_text
        assert "Extract all genes" in prompt.user_text

    def test_paper_text_in_user_text(self):
        prompt = build_extraction_prompt(
            paper_text="Specific paper content here",
            pmid="111",
            max_chars=50000,
        )
        assert "Specific paper content here" in prompt.user_text
        assert 'pmid="111"' in prompt.user_text

    def test_max_chars_truncation(self):
        long_text = "A" * 100_000
        prompt = build_extraction_prompt(
            paper_text=long_text, pmid="111", max_chars=1000
        )
        assert len(prompt.user_text) < 100_000

    def test_pmid_in_document_tag(self):
        prompt = build_extraction_prompt(
            paper_text="Test", pmid="99999999", max_chars=50000
        )
        assert 'pmid="99999999"' in prompt.user_text

    def test_v3_dispatch(self):
        """v3 prompt should include positional candidate filtering content."""
        prompt = build_extraction_prompt(
            paper_text="Test", pmid="111", max_chars=50000, prompt_version="v3"
        )
        assert "positional candidate" in prompt.extraction_instructions
        assert "genomic loci, not individual genes" in prompt.system_prompt

    def test_v4_dispatch(self):
        """v4 prompt should include locus-vs-causal gene and EWAS fixes."""
        prompt = build_extraction_prompt(
            paper_text="Test", pmid="111", max_chars=50000, prompt_version="v4"
        )
        instructions = prompt.extraction_instructions
        # C6ORF195 fix: locus-vs-causal gene instruction
        assert "fine-mapping analysis identifies" in instructions
        assert "LINC01600" in instructions
        assert "C6orf195" in instructions
        # CENPF fix: EWAS check instruction
        assert "EWAS (epigenome-wide association)" in instructions
        assert "methylation analyses" in instructions
        # VWA2 fix: MTAG clarification
        mtag_fix = "MTAG (multi-trait GWAS) reaching genome-wide"
        assert mtag_fix in instructions
        # New example
        assert 'type="include_twas_causal_gene"' in instructions


class TestGemmaV1Prompt:
    """Tests for the gemma_v1 prompt family."""

    def test_gemma_v1_prompt_registered(self):
        assert "gemma_v1" in _PROMPTS
        system, instructions = _PROMPTS["gemma_v1"]
        assert "cerebral small vessel disease" in system.lower()
        # Gemma prompt should be noticeably shorter than v5.
        v5_system, v5_instructions = _PROMPTS["v5"]
        assert len(system) + len(instructions) < 0.6 * (
            len(v5_system) + len(v5_instructions)
        )

    def test_gemma_v1_embeds_json_schema_hint(self):
        system, instructions = _PROMPTS["gemma_v1"]
        combined = system + instructions
        # The schema (or at least its root keys) should be embedded to ground the model.
        schema_hint = json.dumps(ExtractionResult.model_json_schema())
        assert "genes" in schema_hint  # sanity on the schema itself
        assert "JSON" in combined and "gene_symbol" in combined, (
            "gemma_v1 should reference the JSON schema explicitly"
        )

    def test_builder_returns_gemma_v1_parts(self):
        prompt = build_extraction_prompt(
            paper_text="hello",
            pmid="1",
            max_chars=10_000,
            prompt_version="gemma_v1",
        )
        system, instructions = _PROMPTS["gemma_v1"]
        assert prompt.system_prompt == system
        assert prompt.extraction_instructions == instructions
