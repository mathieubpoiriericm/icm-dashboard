"""Tests for pipeline.batch_validation — batch quality checks."""

from __future__ import annotations

from pipeline.batch_validation import batch_validate
from pipeline.llm_extraction import GeneEntry


def _make_entry(
    gene_symbol: str = "NOTCH3",
    confidence: float = 0.85,
    protein_name: str | None = "Notch 3",
    pmid: str = "12345678",
) -> GeneEntry:
    return GeneEntry(
        gene_symbol=gene_symbol,
        confidence=confidence,
        protein_name=protein_name,
        pmid=pmid,
    )


class TestBatchValidateEmpty:
    def test_empty_list(self):
        assert batch_validate([]) == []


class TestPanderaSchema:
    def test_valid_entries_pass(self):
        genes = [_make_entry(), _make_entry(gene_symbol="HTRA1")]
        warnings = batch_validate(genes)
        # Should pass all checks with normal data
        schema_warnings = [w for w in warnings if "Schema" in w]
        assert schema_warnings == []

    def test_empty_gene_symbol_caught(self):
        genes = [_make_entry(gene_symbol="")]
        warnings = batch_validate(genes)
        assert any("Schema" in w or "str_length" in w for w in warnings)


class TestGeneDuplication:
    def test_no_duplication_warning(self):
        genes = [
            _make_entry(gene_symbol="NOTCH3", pmid="111"),
            _make_entry(gene_symbol="HTRA1", pmid="222"),
        ]
        warnings = batch_validate(genes)
        dup_warnings = [w for w in warnings if "extracted from" in w]
        assert dup_warnings == []

    def test_duplication_warning_at_threshold(self):
        """Gene in exactly 4 papers should trigger warning (>3)."""
        genes = [
            _make_entry(gene_symbol="NOTCH3", pmid=f"1111111{i}")
            for i in range(4)
        ]
        warnings = batch_validate(genes)
        assert any("NOTCH3" in w and "4 different papers" in w for w in warnings)

    def test_no_warning_at_3_papers(self):
        """Gene in exactly 3 papers should NOT trigger warning."""
        genes = [
            _make_entry(gene_symbol="NOTCH3", pmid=f"1111111{i}")
            for i in range(3)
        ]
        warnings = batch_validate(genes)
        dup_warnings = [w for w in warnings if "extracted from" in w]
        assert dup_warnings == []

    def test_same_pmid_not_counted_twice(self):
        """Same gene + same PMID should count as 1, not 2."""
        genes = [
            _make_entry(gene_symbol="NOTCH3", pmid="11111111"),
            _make_entry(gene_symbol="NOTCH3", pmid="11111111"),
        ]
        warnings = batch_validate(genes)
        dup_warnings = [w for w in warnings if "extracted from" in w]
        assert dup_warnings == []


class TestConfidenceDistribution:
    def test_normal_confidence_no_warning(self):
        genes = [
            _make_entry(confidence=0.7),
            _make_entry(confidence=0.8, gene_symbol="A"),
            _make_entry(confidence=0.9, gene_symbol="B"),
        ]
        warnings = batch_validate(genes)
        assert not any("Mean confidence" in w for w in warnings)

    def test_high_confidence_warning(self):
        """All genes with confidence 0.99 should trigger warning."""
        genes = [
            _make_entry(confidence=0.99, gene_symbol=f"G{i}", pmid=str(i))
            for i in range(5)
        ]
        warnings = batch_validate(genes)
        assert any("Mean confidence" in w and "0.95" in w for w in warnings)


class TestNullProteinRate:
    def test_low_null_rate_no_warning(self):
        genes = [
            _make_entry(protein_name="P1"),
            _make_entry(protein_name="P2", gene_symbol="A"),
            _make_entry(protein_name="P3", gene_symbol="B"),
            _make_entry(protein_name=None, gene_symbol="C"),
        ]
        warnings = batch_validate(genes)
        null_warnings = [w for w in warnings if "null rate" in w]
        assert null_warnings == []

    def test_high_null_rate_warning(self):
        """More than 30% null protein_name should trigger warning."""
        genes = [_make_entry(protein_name=None, gene_symbol=f"G{i}") for i in range(4)]
        genes.append(_make_entry(protein_name="P1", gene_symbol="OK"))
        warnings = batch_validate(genes)
        assert any("null rate" in w and "30%" in w for w in warnings)


class TestPerPaperGeneCount:
    def test_normal_count_no_warning(self):
        genes = [
            _make_entry(pmid="111", gene_symbol=f"G{i}")
            for i in range(10)
        ]
        warnings = batch_validate(genes)
        assert not any("unusual" in w for w in warnings)

    def test_excessive_genes_warning(self):
        """More than 20 genes from one paper should trigger warning."""
        genes = [
            _make_entry(pmid="111", gene_symbol=f"G{i}")
            for i in range(21)
        ]
        warnings = batch_validate(genes)
        assert any("21 genes" in w and "unusual" in w for w in warnings)


class TestMultipleWarnings:
    def test_accumulates_warnings(self):
        """Multiple checks can fire simultaneously."""
        # High confidence + high null rate + excessive per-paper count
        genes = [
            _make_entry(
                confidence=0.99,
                protein_name=None,
                pmid="111",
                gene_symbol=f"G{i}",
            )
            for i in range(25)
        ]
        warnings = batch_validate(genes)
        assert len(warnings) >= 2  # At least confidence + per-paper count
