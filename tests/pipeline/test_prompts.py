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

    def test_contains_expert_role(self):
        assert "expert" in SYSTEM_PROMPT.lower()


class TestExtractionInstructions:
    def test_contains_task(self):
        assert "TASK:" in EXTRACTION_INSTRUCTIONS

    def test_contains_confidence_scoring(self):
        assert "CONFIDENCE SCORING:" in EXTRACTION_INSTRUCTIONS

    def test_mentions_gwas(self):
        assert "GWAS" in EXTRACTION_INSTRUCTIONS

    def test_mentions_mendelian_randomization(self):
        assert "Mendelian randomization" in EXTRACTION_INSTRUCTIONS

    def test_mentions_omics(self):
        assert "TWAS" in EXTRACTION_INSTRUCTIONS
        assert "PWAS" in EXTRACTION_INSTRUCTIONS
        assert "EWAS" in EXTRACTION_INSTRUCTIONS


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
        assert len(system_blocks) == 1
        assert system_blocks[0]["type"] == "text"
        assert "cache_control" in system_blocks[0]
        assert system_blocks[0]["cache_control"]["type"] == "ephemeral"

    def test_messages_structure(self):
        _, messages = build_extraction_messages(
            paper_text="Test", pmid="111", max_chars=50000
        )
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert isinstance(messages[0]["content"], list)

    def test_user_blocks_have_instructions(self):
        _, messages = build_extraction_messages(
            paper_text="Test", pmid="111", max_chars=50000
        )
        user_blocks = messages[0]["content"]
        assert len(user_blocks) == 2
        # First block = extraction instructions (cached)
        assert "TASK:" in user_blocks[0]["text"]
        assert "cache_control" in user_blocks[0]

    def test_paper_text_in_user_blocks(self):
        _, messages = build_extraction_messages(
            paper_text="Specific paper content here",
            pmid="111",
            max_chars=50000,
        )
        user_blocks = messages[0]["content"]
        # Second block = paper text (not cached)
        assert "Specific paper content here" in user_blocks[1]["text"]
        assert "PMID: 111" in user_blocks[1]["text"]

    def test_max_chars_truncation(self):
        long_text = "A" * 100_000
        _, messages = build_extraction_messages(
            paper_text=long_text, pmid="111", max_chars=1000
        )
        user_blocks = messages[0]["content"]
        # Paper text block should be truncated
        assert len(user_blocks[1]["text"]) < 100_000

    def test_pmid_in_paper_block(self):
        _, messages = build_extraction_messages(
            paper_text="Test", pmid="99999999", max_chars=50000
        )
        user_blocks = messages[0]["content"]
        assert "99999999" in user_blocks[1]["text"]
