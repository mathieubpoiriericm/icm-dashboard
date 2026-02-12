"""Tests for pipeline.data_merger — pure helpers and async merge logic."""

from __future__ import annotations

from pipeline.data_merger import (
    _build_gene_data,
    dedupe_list,
    ensure_list,
    format_omics,
    merge_gene_entries,
)

# ---------------------------------------------------------------------------
# ensure_list
# ---------------------------------------------------------------------------


class TestEnsureList:
    def test_none_returns_empty(self):
        assert ensure_list(None) == []

    def test_string_returns_singleton(self):
        assert ensure_list("WMH") == ["WMH"]

    def test_list_passthrough(self):
        assert ensure_list(["WMH", "SVS"]) == ["WMH", "SVS"]

    def test_empty_list(self):
        assert ensure_list([]) == []

    def test_int_wrapped(self):
        assert ensure_list(42) == [42]

    def test_bool_wrapped(self):
        assert ensure_list(True) == [True]

    def test_empty_string(self):
        assert ensure_list("") == [""]


# ---------------------------------------------------------------------------
# dedupe_list
# ---------------------------------------------------------------------------


class TestDedupeList:
    def test_no_duplicates(self):
        assert dedupe_list(["a", "b", "c"]) == ["a", "b", "c"]

    def test_with_duplicates(self):
        assert dedupe_list(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]

    def test_empty(self):
        assert dedupe_list([]) == []

    def test_all_same(self):
        assert dedupe_list(["x", "x", "x"]) == ["x"]

    def test_preserves_order(self):
        assert dedupe_list(["c", "a", "b", "a"]) == ["c", "a", "b"]

    def test_with_ints(self):
        assert dedupe_list([1, 2, 1, 3]) == [1, 2, 3]


# ---------------------------------------------------------------------------
# format_omics
# ---------------------------------------------------------------------------


class TestFormatOmics:
    def test_empty(self):
        assert format_omics([]) == ""

    def test_single(self):
        assert format_omics(["TWAS"]) == "TWAS*;"

    def test_multiple(self):
        assert format_omics(["TWAS", "PWAS"]) == "TWAS*;PWAS*;"

    def test_three_types(self):
        result = format_omics(["TWAS", "PWAS", "EWAS"])
        assert result == "TWAS*;PWAS*;EWAS*;"


# ---------------------------------------------------------------------------
# _build_gene_data
# ---------------------------------------------------------------------------


class TestBuildGeneData:
    def test_basic_fields(self, sample_gene_entry):
        data = _build_gene_data(sample_gene_entry)
        assert data["gene"] == "NOTCH3"
        assert data["protein"] == "Notch receptor 3"
        assert data["references"] == "12345678"

    def test_protein_fallback_to_gene(self, make_gene_entry):
        entry = make_gene_entry(protein_name=None)
        data = _build_gene_data(entry)
        assert data["protein"] == "NOTCH3"

    def test_gwas_trait_joined(self, make_gene_entry):
        entry = make_gene_entry(gwas_trait=["WMH", "SVS", "WMH"])
        data = _build_gene_data(entry)
        # Should dedupe
        assert data["gwas_trait"] == "WMH, SVS"

    def test_mendelian_randomization_yes(self, make_gene_entry):
        entry = make_gene_entry(mendelian_randomization=True)
        data = _build_gene_data(entry)
        assert data["mendelian_randomization"] == "Y"

    def test_mendelian_randomization_no(self, make_gene_entry):
        entry = make_gene_entry(mendelian_randomization=False)
        data = _build_gene_data(entry)
        assert data["mendelian_randomization"] == ""

    def test_omics_evidence_formatted(self, make_gene_entry):
        entry = make_gene_entry(omics_evidence=["TWAS", "PWAS"])
        data = _build_gene_data(entry)
        assert data["evidence_from_other_omics_studies"] == "TWAS*;PWAS*;"

    def test_empty_defaults(self, make_gene_entry):
        entry = make_gene_entry()
        data = _build_gene_data(entry)
        assert data["chromosomal_location"] == ""
        assert data["link_to_monogenetic_disease"] == ""
        assert data["brain_cell_types"] == ""
        assert data["affected_pathway"] == ""


# ---------------------------------------------------------------------------
# merge_gene_entries (async, mocked DB)
# ---------------------------------------------------------------------------


class TestMergeGeneEntries:
    async def test_empty_input(self):
        result = await merge_gene_entries([])
        assert result == {"inserted": 0, "updated": 0}

    async def test_new_genes_inserted(self, sample_gene_entries, mocker):
        mocker.patch(
            "pipeline.data_merger.get_existing_genes",
            return_value=set(),
        )
        mocker.patch(
            "pipeline.data_merger.merge_genes_transactional",
            return_value=(3, 0),
        )
        result = await merge_gene_entries(sample_gene_entries)
        assert result["inserted"] == 3
        assert result["updated"] == 0

    async def test_existing_genes_updated(self, sample_gene_entries, mocker):
        mocker.patch(
            "pipeline.data_merger.get_existing_genes",
            return_value={"NOTCH3", "HTRA1", "COL4A1"},
        )
        mocker.patch(
            "pipeline.data_merger.merge_genes_transactional",
            return_value=(0, 3),
        )
        result = await merge_gene_entries(sample_gene_entries)
        assert result["inserted"] == 0
        assert result["updated"] == 3

    async def test_mix_insert_and_update(self, sample_gene_entries, mocker):
        mocker.patch(
            "pipeline.data_merger.get_existing_genes",
            return_value={"NOTCH3"},
        )
        mocker.patch(
            "pipeline.data_merger.merge_genes_transactional",
            return_value=(2, 1),
        )
        result = await merge_gene_entries(sample_gene_entries)
        assert result["inserted"] == 2
        assert result["updated"] == 1

    async def test_deduplicates_within_batch(self, make_gene_entry, mocker):
        """Two entries with same gene symbol should not both be inserted."""
        entries = [
            make_gene_entry(gene_symbol="NOTCH3", pmid="111"),
            make_gene_entry(gene_symbol="NOTCH3", pmid="222"),
        ]
        mocker.patch(
            "pipeline.data_merger.get_existing_genes",
            return_value=set(),
        )
        mock_merge = mocker.patch(
            "pipeline.data_merger.merge_genes_transactional",
            return_value=(1, 1),
        )
        await merge_gene_entries(entries)
        # First NOTCH3 -> insert, second -> update (because first adds to set)
        call_args = mock_merge.call_args
        to_insert = call_args[0][0]
        to_update = call_args[0][1]
        assert len(to_insert) == 1
        assert len(to_update) == 1
