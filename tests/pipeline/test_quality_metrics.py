"""Tests for pipeline.quality_metrics — TokenUsage, PipelineMetrics."""

from __future__ import annotations

import json

from pipeline.quality_metrics import (
    PipelineMetrics,
    TokenUsage,
    accumulate_usage,
)

# ---------------------------------------------------------------------------
# TokenUsage
# ---------------------------------------------------------------------------


class TestTokenUsage:
    def test_defaults_zero(self):
        tu = TokenUsage()
        assert tu.input_tokens == 0
        assert tu.output_tokens == 0
        assert tu.cache_creation_input_tokens == 0
        assert tu.cache_read_input_tokens == 0

    def test_total_tokens(self):
        tu = TokenUsage(input_tokens=100, output_tokens=50)
        assert tu.total_tokens == 150

    def test_cache_hit_rate_zero_input(self):
        tu = TokenUsage()
        assert tu.cache_hit_rate == 0.0

    def test_cache_hit_rate_calculated(self):
        tu = TokenUsage(input_tokens=80, cache_read_input_tokens=20)
        assert tu.cache_hit_rate == pytest.approx(0.2)

    def test_cache_hit_rate_all_cached(self):
        tu = TokenUsage(input_tokens=0, cache_read_input_tokens=100)
        assert tu.cache_hit_rate == 1.0

    def test_iadd(self):
        a = TokenUsage(input_tokens=100, output_tokens=50)
        b = TokenUsage(
            input_tokens=200,
            output_tokens=100,
            cache_creation_input_tokens=10,
            cache_read_input_tokens=20,
        )
        a += b
        assert a.input_tokens == 300
        assert a.output_tokens == 150
        assert a.cache_creation_input_tokens == 10
        assert a.cache_read_input_tokens == 20

    def test_iadd_returns_self(self):
        a = TokenUsage(input_tokens=10)
        b = TokenUsage(input_tokens=5)
        result = a.__iadd__(b)
        assert result is a

    def test_iadd_chain(self):
        total = TokenUsage()
        for _i in range(5):
            total += TokenUsage(input_tokens=10, output_tokens=5)
        assert total.input_tokens == 50
        assert total.output_tokens == 25


# ---------------------------------------------------------------------------
# accumulate_usage
# ---------------------------------------------------------------------------


class TestAccumulateUsage:
    def test_accumulate_from_response(self, mock_anthropic_response):
        usage = TokenUsage()
        resp = mock_anthropic_response(input_tokens=500, output_tokens=200)
        accumulate_usage(usage, resp)
        assert usage.input_tokens == 500
        assert usage.output_tokens == 200

    def test_accumulate_no_usage_attr(self):
        usage = TokenUsage()

        class NoUsage:
            pass

        accumulate_usage(usage, NoUsage())
        assert usage.total_tokens == 0

    def test_accumulate_none_usage(self):
        usage = TokenUsage()

        class NullUsage:
            usage = None

        accumulate_usage(usage, NullUsage())
        assert usage.total_tokens == 0

    def test_accumulate_with_cache_tokens(self):
        usage = TokenUsage()

        class CachedResponse:
            class usage:
                input_tokens = 100
                output_tokens = 50
                cache_creation_input_tokens = 30
                cache_read_input_tokens = 20

        accumulate_usage(usage, CachedResponse())
        assert usage.cache_creation_input_tokens == 30
        assert usage.cache_read_input_tokens == 20

    def test_accumulate_multiple(self, mock_anthropic_response):
        usage = TokenUsage()
        for _ in range(3):
            resp = mock_anthropic_response(input_tokens=100, output_tokens=50)
            accumulate_usage(usage, resp)
        assert usage.input_tokens == 300
        assert usage.output_tokens == 150


# ---------------------------------------------------------------------------
# PipelineMetrics
# ---------------------------------------------------------------------------


class TestPipelineMetrics:
    def test_defaults_zero(self):
        m = PipelineMetrics()
        assert m.papers_processed == 0
        assert m.genes_extracted == 0
        assert m.genes_validated == 0
        assert m.genes_rejected == 0

    def test_gene_acceptance_rate_zero(self, empty_metrics):
        assert empty_metrics.gene_acceptance_rate == 0.0

    def test_gene_acceptance_rate_calculated(self, populated_metrics):
        # 20/25 = 0.8
        assert populated_metrics.gene_acceptance_rate == pytest.approx(0.8)

    def test_fulltext_rate_zero(self, empty_metrics):
        assert empty_metrics.fulltext_rate == 0.0

    def test_fulltext_rate_calculated(self, populated_metrics):
        # 7/10 = 0.7
        assert populated_metrics.fulltext_rate == pytest.approx(0.7)

    def test_total_genes_processed(self, populated_metrics):
        assert populated_metrics.total_genes_processed == 25  # 20 + 5

    def test_summary_contains_key_info(self, populated_metrics):
        s = populated_metrics.summary()
        assert "Pipeline Metrics:" in s
        assert "10 processed" in s
        assert "25 extracted" in s
        assert "20 validated" in s

    def test_summary_includes_tokens(self, populated_metrics):
        s = populated_metrics.summary()
        assert "50,000 input" in s
        assert "10,000 output" in s

    def test_summary_includes_cache(self, populated_metrics):
        s = populated_metrics.summary()
        assert "Cache:" in s
        assert "15,000 read" in s

    def test_build_report_structure(self, populated_metrics):
        report = populated_metrics.build_report()
        assert "timestamp" in report
        assert "papers" in report
        assert "genes" in report
        assert "token_usage" in report
        assert report["papers"]["processed"] == 10
        assert report["genes"]["validated"] == 20

    def test_build_report_json_serializable(self, populated_metrics):
        report = populated_metrics.build_report()
        serialized = json.dumps(report)
        assert isinstance(serialized, str)

    def test_write_json_report(self, populated_metrics, tmp_path):
        path = populated_metrics.write_json_report(tmp_path)
        assert path.exists()
        assert path.suffix == ".json"
        data = json.loads(path.read_text())
        assert data["papers"]["processed"] == 10

    def test_write_json_report_creates_dir(self, populated_metrics, tmp_path):
        log_dir = tmp_path / "nested" / "logs"
        path = populated_metrics.write_json_report(log_dir)
        assert path.exists()
        assert log_dir.is_dir()


import pytest  # noqa: E402
