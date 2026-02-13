"""Tests for pipeline.prompts — message structure and content checks."""

from __future__ import annotations

from pipeline.prompts import (
    EXTRACTION_INSTRUCTIONS,
    SYSTEM_PROMPT,
    build_extraction_messages,
)


class TestSystemPrompt:
    def test_contains_csvd(self):
        assert "cSVD" in SYSTEM_PROMPT

    def test_contains_role(self):
        assert "systematic reviewer" in SYSTEM_PROMPT

    def test_mentions_causal_distinction(self):
        assert "causal" in SYSTEM_PROMPT
        assert "association" in SYSTEM_PROMPT


class TestExtractionInstructions:
    def test_contains_task_xml(self):
        assert "<task>" in EXTRACTION_INSTRUCTIONS

    def test_contains_confidence_scoring_xml(self):
        assert "<confidence_scoring>" in EXTRACTION_INSTRUCTIONS

    def test_mentions_gwas(self):
        assert "GWAS" in EXTRACTION_INSTRUCTIONS

    def test_mentions_mendelian_randomization(self):
        assert "Mendelian randomization" in EXTRACTION_INSTRUCTIONS

    def test_mentions_omics(self):
        assert "TWAS" in EXTRACTION_INSTRUCTIONS
        assert "PWAS" in EXTRACTION_INSTRUCTIONS
        assert "EWAS" in EXTRACTION_INSTRUCTIONS

    def test_has_xml_structure(self):
        assert "<instructions>" in EXTRACTION_INSTRUCTIONS
        assert "</instructions>" in EXTRACTION_INSTRUCTIONS
        assert "<inclusion_criteria>" in EXTRACTION_INSTRUCTIONS
        assert "<extraction_strategy>" in EXTRACTION_INSTRUCTIONS
        assert "<field_guidance>" in EXTRACTION_INSTRUCTIONS

    def test_has_examples(self):
        assert "<examples>" in EXTRACTION_INSTRUCTIONS
        assert 'type="include_validated"' in EXTRACTION_INSTRUCTIONS
        assert 'type="include_high_confidence"' in EXTRACTION_INSTRUCTIONS
        assert 'type="exclude_general_stroke"' in EXTRACTION_INSTRUCTIONS
        assert 'type="exclude_pathway_only"' in EXTRACTION_INSTRUCTIONS
        assert 'type="exclude_background_monogenic"' in EXTRACTION_INSTRUCTIONS

    def test_gwas_trait_vocabulary(self):
        """GWAS traits in prompt should use canonical abbreviations."""
        assert "WMH" in EXTRACTION_INSTRUCTIONS
        assert "DWMH" in EXTRACTION_INSTRUCTIONS
        assert "PVWMH" in EXTRACTION_INSTRUCTIONS
        assert "SVS" in EXTRACTION_INSTRUCTIONS
        assert "BG-PVS" in EXTRACTION_INSTRUCTIONS
        assert "WM-PVS" in EXTRACTION_INSTRUCTIONS
        assert "HIP-PVS" in EXTRACTION_INSTRUCTIONS
        assert "PSMD" in EXTRACTION_INSTRUCTIONS
        assert "MD" in EXTRACTION_INSTRUCTIONS
        assert "extreme-cSVD" in EXTRACTION_INSTRUCTIONS
        assert "FA" in EXTRACTION_INSTRUCTIONS
        assert "ICH-lobar" in EXTRACTION_INSTRUCTIONS
        assert "ICH-non-lobar" in EXTRACTION_INSTRUCTIONS
        assert "DTI-ALPS" in EXTRACTION_INSTRUCTIONS
        assert "ICVF" in EXTRACTION_INSTRUCTIONS
        assert "ISOVF" in EXTRACTION_INSTRUCTIONS
        assert "WMH-cortical-atrophy" in EXTRACTION_INSTRUCTIONS
        assert "WM-BAG" in EXTRACTION_INSTRUCTIONS
        assert "retinal-vessels" in EXTRACTION_INSTRUCTIONS

    def test_grounding_instruction(self):
        """Should instruct the model to verify evidence before extracting."""
        assert "Identify all passages" in EXTRACTION_INSTRUCTIONS
        assert "Verify" in EXTRACTION_INSTRUCTIONS


class TestBuildExtractionMessages:
    def test_returns_tuple(self):
        system_blocks, messages = build_extraction_messages(
            paper_text="Test paper", pmid="12345678", max_chars=50000
        )
        assert isinstance(system_blocks, list)
        assert isinstance(messages, list)

    def test_system_blocks_structure(self):
        system_blocks, _ = build_extraction_messages(
            paper_text="Test", pmid="111", max_chars=50000
        )
        assert len(system_blocks) == 2
        # First block = system prompt
        assert system_blocks[0]["type"] == "text"
        assert "cache_control" in system_blocks[0]
        assert system_blocks[0]["cache_control"]["type"] == "ephemeral"
        # Second block = extraction instructions
        assert system_blocks[1]["type"] == "text"
        assert "cache_control" in system_blocks[1]
        assert "<instructions>" in system_blocks[1]["text"]

    def test_system_blocks_contain_instructions(self):
        system_blocks, _ = build_extraction_messages(
            paper_text="Test", pmid="111", max_chars=50000
        )
        assert SYSTEM_PROMPT in system_blocks[0]["text"]
        assert EXTRACTION_INSTRUCTIONS in system_blocks[1]["text"]

    def test_messages_structure(self):
        _, messages = build_extraction_messages(
            paper_text="Test", pmid="111", max_chars=50000
        )
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert isinstance(messages[0]["content"], list)

    def test_user_blocks_contain_document(self):
        _, messages = build_extraction_messages(
            paper_text="Test", pmid="111", max_chars=50000
        )
        user_blocks = messages[0]["content"]
        assert len(user_blocks) == 1
        assert "<document" in user_blocks[0]["text"]
        assert "Extract all genes" in user_blocks[0]["text"]

    def test_paper_text_in_user_blocks(self):
        _, messages = build_extraction_messages(
            paper_text="Specific paper content here",
            pmid="111",
            max_chars=50000,
        )
        user_blocks = messages[0]["content"]
        assert "Specific paper content here" in user_blocks[0]["text"]
        assert 'pmid="111"' in user_blocks[0]["text"]

    def test_max_chars_truncation(self):
        long_text = "A" * 100_000
        _, messages = build_extraction_messages(
            paper_text=long_text, pmid="111", max_chars=1000
        )
        user_blocks = messages[0]["content"]
        # Paper text block should be truncated
        assert len(user_blocks[0]["text"]) < 100_000

    def test_pmid_in_document_tag(self):
        _, messages = build_extraction_messages(
            paper_text="Test", pmid="99999999", max_chars=50000
        )
        user_blocks = messages[0]["content"]
        assert 'pmid="99999999"' in user_blocks[0]["text"]

    def test_user_blocks_not_cached(self):
        """User blocks (paper text) should not have cache_control."""
        _, messages = build_extraction_messages(
            paper_text="Test", pmid="111", max_chars=50000
        )
        user_blocks = messages[0]["content"]
        assert "cache_control" not in user_blocks[0]
