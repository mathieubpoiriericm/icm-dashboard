"""Unit tests for scripts.finetune.build_dataset."""

from __future__ import annotations

import json

from scripts.finetune.build_dataset import (
    Paper,
    attach_pdf_text,
    extract_paper_records,
    load_reports,
    paper_to_chat_record,
)


def _make_report(timestamp: str, papers: list[dict]) -> dict:
    return {"timestamp": timestamp, "papers_detail": papers}


def _make_paper_detail(pmid: str, genes: list[dict]) -> dict:
    return {
        "pmid": pmid,
        "fulltext": True,
        "gene_count": len(genes),
        "genes": genes,
        "rejected_genes": [],
    }


def _gene(symbol: str, confidence: float = 0.9, **extras) -> dict:
    return {
        "gene_symbol": symbol,
        "protein_name": None,
        "gwas_trait": [],
        "mendelian_randomization": False,
        "omics_evidence": [],
        "confidence": confidence,
        "causal_evidence_summary": None,
        "pmid": "",
        **extras,
    }


class TestLoadReports:
    def test_reads_multiple_files(self, tmp_path):
        (tmp_path / "r1.json").write_text(
            json.dumps(_make_report("2026-01-01T00:00:00Z", []))
        )
        (tmp_path / "r2.json").write_text(
            json.dumps(_make_report("2026-02-01T00:00:00Z", []))
        )
        out = load_reports(str(tmp_path / "*.json"))
        assert len(out) == 2


class TestExtractPaperRecords:
    def test_low_confidence_genes_dropped(self):
        reports = [_make_report("2026-01-01T00:00:00Z", [
            _make_paper_detail("1", [_gene("A", 0.9), _gene("B", 0.5)]),
        ])]
        out = extract_paper_records(reports, min_confidence=0.7, gold_pmids=set())
        assert len(out) == 1
        pmid, genes = out[0]
        assert pmid == "1"
        assert [g["gene_symbol"] for g in genes] == ["A"]

    def test_paper_dropped_if_no_genes_pass_filter(self):
        reports = [_make_report("2026-01-01T00:00:00Z", [
            _make_paper_detail("1", [_gene("A", 0.3)]),
        ])]
        out = extract_paper_records(reports, min_confidence=0.7, gold_pmids=set())
        assert out == []

    def test_gold_pmids_excluded(self):
        reports = [_make_report("2026-01-01T00:00:00Z", [
            _make_paper_detail("1", [_gene("A", 0.9)]),
            _make_paper_detail("2", [_gene("B", 0.9)]),
        ])]
        out = extract_paper_records(reports, min_confidence=0.7, gold_pmids={"2"})
        assert [r[0] for r in out] == ["1"]

    def test_latest_report_wins_on_pmid_collision(self):
        older = _make_report(
            "2026-01-01T00:00:00Z",
            [_make_paper_detail("1", [_gene("OLD", 0.9)])],
        )
        newer = _make_report(
            "2026-02-01T00:00:00Z",
            [_make_paper_detail("1", [_gene("NEW", 0.9)])],
        )
        out = extract_paper_records(
            [older, newer], min_confidence=0.7, gold_pmids=set()
        )
        assert [g["gene_symbol"] for _, g in out for g in [g[0]]] == ["NEW"]

    def test_empty_gene_list_dropped(self):
        reports = [_make_report("2026-01-01T00:00:00Z", [
            _make_paper_detail("1", []),
        ])]
        out = extract_paper_records(reports, min_confidence=0.7, gold_pmids=set())
        assert out == []


class TestAttachPdfText:
    def test_missing_pdf_is_skipped(self, tmp_path):
        records = [("1", [_gene("A", 0.9)])]
        out = attach_pdf_text(records, tmp_path, max_paper_chars=10_000)
        # No 1.pdf in tmp_path → dropped.
        assert out == []

    def test_parses_and_truncates(self, tmp_path, monkeypatch):
        # Mock parse_local_pdf to return deterministic text.
        from scripts.finetune import build_dataset as bd

        def fake_parse(path):
            if path.stem == "1":
                return "lorem ipsum " * 500  # ~5500 chars
            return None

        monkeypatch.setattr(bd, "parse_local_pdf", fake_parse)
        (tmp_path / "1.pdf").write_bytes(b"stub")
        records = [("1", [_gene("A", 0.9)])]
        out = attach_pdf_text(records, tmp_path, max_paper_chars=1000)
        assert len(out) == 1
        assert out[0].pmid == "1"
        assert len(out[0].fulltext) <= 1000


class TestPaperToChatRecord:
    def test_shape(self):
        p = Paper(pmid="42", fulltext="sample text", genes=[_gene("NOTCH3", 0.9)])
        record = paper_to_chat_record(p)
        assert set(record.keys()) == {"messages"}
        roles = [m["role"] for m in record["messages"]]
        assert roles == ["system", "user", "assistant"]

    def test_system_is_ollama_v1(self):
        p = Paper(pmid="42", fulltext="sample", genes=[_gene("A", 0.9)])
        record = paper_to_chat_record(p)
        sys_content = record["messages"][0]["content"]
        assert "cerebral small vessel disease" in sys_content.lower()

    def test_user_contains_paper_text_and_pmid(self):
        p = Paper(pmid="42", fulltext="unique_marker_text", genes=[_gene("A", 0.9)])
        record = paper_to_chat_record(p)
        user_content = record["messages"][1]["content"]
        assert "unique_marker_text" in user_content
        assert "42" in user_content

    def test_assistant_is_valid_json_matching_schema(self):
        p = Paper(pmid="42", fulltext="text", genes=[_gene("NOTCH3", 0.9)])
        record = paper_to_chat_record(p)
        payload = json.loads(record["messages"][2]["content"])
        assert payload["genes"][0]["gene_symbol"] == "NOTCH3"

    def test_assistant_strips_pmid_from_gene_dict(self):
        """The training target should NOT include pmid in each gene; the
        serving path adds it post-extraction."""
        p = Paper(pmid="42", fulltext="t", genes=[_gene("A", 0.9, pmid="42")])
        record = paper_to_chat_record(p)
        payload = json.loads(record["messages"][2]["content"])
        assert "pmid" not in payload["genes"][0]
