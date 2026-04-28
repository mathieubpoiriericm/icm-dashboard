"""Tests for results viewer pure functions."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest
from pipeline_app.pages.results_viewer import (
    _MAX_REPORT_SIZE,
    _SAFE_REPORT_ID,
    _find_report,
    _gene_confidence,
    _gene_symbol,
)


class TestGeneSymbol:
    def test_uses_gene_symbol_key(self):
        assert _gene_symbol({"gene_symbol": "BRCA1"}) == "BRCA1"

    def test_falls_back_to_symbol_key(self):
        assert _gene_symbol({"symbol": "TP53"}) == "TP53"

    def test_prefers_gene_symbol_over_symbol(self):
        assert _gene_symbol({"gene_symbol": "A", "symbol": "B"}) == "A"

    def test_returns_empty_when_neither_key(self):
        assert _gene_symbol({}) == ""

    def test_returns_empty_for_unrelated_keys(self):
        assert _gene_symbol({"name": "BRCA1"}) == ""


class TestGeneConfidence:
    def test_uses_confidence_score_key(self):
        assert _gene_confidence({"confidence_score": 0.95}) == 0.95

    def test_falls_back_to_confidence_key(self):
        assert _gene_confidence({"confidence": 0.87}) == 0.87

    def test_prefers_confidence_score_over_confidence(self):
        result = _gene_confidence({"confidence_score": 0.9, "confidence": 0.5})
        assert result == 0.9

    def test_rounds_to_three_decimals(self):
        assert _gene_confidence({"confidence_score": 0.12345}) == 0.123

    def test_returns_zero_when_no_key(self):
        assert _gene_confidence({}) == 0

    def test_rounds_correctly(self):
        assert _gene_confidence({"confidence_score": 0.1236}) == 0.124


class TestFindReport:
    def test_exact_match(self, tmp_path: Path):
        logs_json = tmp_path / "logs" / "json"
        logs_json.mkdir(parents=True)
        report = logs_json / "pipeline_report_20260101.json"
        report.write_text("{}")
        result = _find_report(str(tmp_path), "pipeline_report_20260101")
        assert result == report

    def test_partial_match(self, tmp_path: Path):
        logs_json = tmp_path / "logs" / "json"
        logs_json.mkdir(parents=True)
        report = logs_json / "pipeline_report_20260101_120000.json"
        report.write_text("{}")
        result = _find_report(str(tmp_path), "20260101")
        assert result == report

    def test_returns_none_when_no_logs_dir(self, tmp_path: Path):
        result = _find_report(str(tmp_path), "any_id")
        assert result is None

    def test_returns_none_when_no_match(self, tmp_path: Path):
        logs_json = tmp_path / "logs" / "json"
        logs_json.mkdir(parents=True)
        (logs_json / "pipeline_report_other.json").write_text("{}")
        result = _find_report(str(tmp_path), "nonexistent_id")
        assert result is None

    def test_returns_newest_partial_match(self, tmp_path: Path):
        logs_json = tmp_path / "logs" / "json"
        logs_json.mkdir(parents=True)
        old = logs_json / "pipeline_report_20260101.json"
        old.write_text("{}")
        os.utime(old, (time.time() - 60, time.time() - 60))
        new = logs_json / "pipeline_report_20260102.json"
        new.write_text("{}")
        result = _find_report(str(tmp_path), "pipeline_report")
        assert result == new

    def test_ignores_non_json_files(self, tmp_path: Path):
        logs_json = tmp_path / "logs" / "json"
        logs_json.mkdir(parents=True)
        (logs_json / "pipeline_report_test.csv").write_text("data")
        result = _find_report(str(tmp_path), "pipeline_report_test")
        assert result is None

    def test_empty_project_root(self):
        result = _find_report("", "nonexistent_id")
        assert result is None


class TestFindReportPartialMatchPolicy:
    """Regression: partial match must prefer prefix, substring only if unique."""

    def test_ambiguous_substring_returns_none(self, tmp_path: Path):
        logs_json = tmp_path / "logs" / "json"
        logs_json.mkdir(parents=True)
        # Two files both CONTAIN "test" but neither STARTS with it.
        (logs_json / "prefix_a_test.json").write_text("{}")
        (logs_json / "prefix_b_test.json").write_text("{}")
        result = _find_report(str(tmp_path), "test")
        assert result is None

    def test_prefix_beats_substring_when_both_exist(self, tmp_path: Path):
        logs_json = tmp_path / "logs" / "json"
        logs_json.mkdir(parents=True)
        prefix_hit = logs_json / "test_1.json"
        prefix_hit.write_text("{}")
        substring_only = logs_json / "latest_test_2.json"
        substring_only.write_text("{}")
        # Even if the substring-only file is newer, the prefix hit wins.
        os.utime(
            substring_only,
            (time.time() + 10, time.time() + 10),
        )
        result = _find_report(str(tmp_path), "test")
        assert result == prefix_hit

    def test_unique_substring_still_resolves(self, tmp_path: Path):
        logs_json = tmp_path / "logs" / "json"
        logs_json.mkdir(parents=True)
        # No prefix hit; only one substring hit.
        only = logs_json / "prefix_only_test.json"
        only.write_text("{}")
        result = _find_report(str(tmp_path), "test")
        assert result == only


class TestFindReportSymlinkSafety:
    """Regression: _find_report must skip symlinks to prevent path escapes."""

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlinks")
    def test_skips_symlink_in_exact_match(self, tmp_path: Path):
        logs_json = tmp_path / "logs" / "json"
        logs_json.mkdir(parents=True)
        # Real file outside the project root
        outside = tmp_path / "outside.json"
        outside.write_text('{"secret": "data"}')
        # Symlink inside logs/json pointing at the outside file
        link = logs_json / "pipeline_report_evil.json"
        link.symlink_to(outside)
        # Exact-match lookup must not return the symlink
        result = _find_report(str(tmp_path), "pipeline_report_evil")
        assert result is None

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlinks")
    def test_skips_symlink_in_partial_match(self, tmp_path: Path):
        logs_json = tmp_path / "logs" / "json"
        logs_json.mkdir(parents=True)
        outside = tmp_path / "outside.json"
        outside.write_text('{"secret": "data"}')
        link = logs_json / "pipeline_report_link.json"
        link.symlink_to(outside)
        # Partial match for any substring must skip the symlink
        result = _find_report(str(tmp_path), "report_link")
        assert result is None

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlinks")
    def test_returns_real_file_alongside_symlink(self, tmp_path: Path):
        logs_json = tmp_path / "logs" / "json"
        logs_json.mkdir(parents=True)
        outside = tmp_path / "outside.json"
        outside.write_text("{}")
        # Add both a symlink and a real matching file
        (logs_json / "pipeline_report_link.json").symlink_to(outside)
        real = logs_json / "pipeline_report_real.json"
        real.write_text("{}")
        result = _find_report(str(tmp_path), "pipeline_report")
        assert result == real

    def test_skips_file_that_disappears_during_scan(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        logs_json = tmp_path / "logs" / "json"
        logs_json.mkdir(parents=True)
        good = logs_json / "pipeline_report_good.json"
        good.write_text("{}")
        rotated = logs_json / "pipeline_report_rotated.json"
        rotated.write_text("{}")

        real_stat = Path.stat

        def flaky_stat(self: Path, *args, **kwargs):
            if self == rotated:
                raise FileNotFoundError(str(self))
            return real_stat(self, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", flaky_stat)
        result = _find_report(str(tmp_path), "pipeline_report")
        assert result == good


class TestMaxReportSize:
    """Regression: report-size cap exists to prevent OOM on huge JSONs."""

    def test_cap_is_50mb(self):
        assert _MAX_REPORT_SIZE == 50 * 1024 * 1024


class TestSafeReportIdRegex:
    def test_valid_alphanumeric(self):
        assert _SAFE_REPORT_ID.match("pipeline_report_20260101") is not None

    def test_valid_with_hyphens(self):
        assert _SAFE_REPORT_ID.match("report-2026-01-01") is not None

    def test_valid_with_underscores(self):
        assert _SAFE_REPORT_ID.match("report_2026_01_01") is not None

    def test_rejects_path_traversal(self):
        assert _SAFE_REPORT_ID.match("../../../etc/passwd") is None

    def test_rejects_spaces(self):
        assert _SAFE_REPORT_ID.match("report with spaces") is None

    def test_rejects_special_chars(self):
        assert _SAFE_REPORT_ID.match("report;rm -rf /") is None

    def test_rejects_empty(self):
        assert _SAFE_REPORT_ID.match("") is None

    def test_rejects_slashes(self):
        assert _SAFE_REPORT_ID.match("dir/report") is None
