"""Tests for tuning history pure functions."""

from __future__ import annotations

import logging
from pathlib import Path

from pipeline_app.pages.tuning_history import (
    NUMERIC_COLUMNS,
    _compute_display_keys,
    _diff_value,
    _load_tuning_runs,
)


class TestDiffValue:
    def test_positive_diff(self):
        assert _diff_value("0.5", "0.8", "precision") == "+0.3000"

    def test_negative_diff(self):
        assert _diff_value("0.8", "0.5", "precision") == "-0.3000"

    def test_zero_diff(self):
        assert _diff_value("0.5", "0.5", "precision") == "0.0000"

    def test_non_numeric_column_returns_empty(self):
        assert _diff_value("0.5", "0.8", "notes") == ""

    def test_non_numeric_column_with_numbers(self):
        assert _diff_value("1", "2", "llm_model") == ""

    def test_unparseable_values_return_empty(self):
        assert _diff_value("abc", "def", "precision") == ""

    def test_mixed_parseable_returns_empty(self):
        assert _diff_value("0.5", "abc", "recall") == ""

    def test_integer_values(self):
        assert _diff_value("10", "15", "tp") == "+5.0000"

    def test_large_precision_diff(self):
        assert _diff_value("0.12345678", "0.87654321", "f1") == "+0.7531"


class TestLoadTuningRuns:
    def test_returns_empty_when_no_file(self, tmp_path: Path):
        assert _load_tuning_runs(str(tmp_path)) == []

    def test_loads_and_reverses_order(self, tmp_path: Path):
        csv_dir = tmp_path / "logs" / "tuning"
        csv_dir.mkdir(parents=True)
        (csv_dir / "tuning_runs.csv").write_text(
            "timestamp,precision,recall\n2026-01-01,0.8,0.9\n2026-01-02,0.85,0.95\n"
        )
        rows = _load_tuning_runs(str(tmp_path))
        assert len(rows) == 2
        assert rows[0]["timestamp"] == "2026-01-02"
        assert rows[1]["timestamp"] == "2026-01-01"

    def test_headers_only_returns_empty(self, tmp_path: Path):
        csv_dir = tmp_path / "logs" / "tuning"
        csv_dir.mkdir(parents=True)
        (csv_dir / "tuning_runs.csv").write_text("timestamp,precision\n")
        assert _load_tuning_runs(str(tmp_path)) == []

    def test_single_row(self, tmp_path: Path):
        csv_dir = tmp_path / "logs" / "tuning"
        csv_dir.mkdir(parents=True)
        (csv_dir / "tuning_runs.csv").write_text(
            "timestamp,precision\n2026-01-01,0.8\n"
        )
        rows = _load_tuning_runs(str(tmp_path))
        assert len(rows) == 1
        assert rows[0]["precision"] == "0.8"

    def test_empty_project_root(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert _load_tuning_runs("") == []

    def test_preserves_all_columns(self, tmp_path: Path):
        csv_dir = tmp_path / "logs" / "tuning"
        csv_dir.mkdir(parents=True)
        (csv_dir / "tuning_runs.csv").write_text(
            "timestamp,model,precision,notes\n2026-01-01,opus,0.8,test run\n"
        )
        rows = _load_tuning_runs(str(tmp_path))
        assert rows[0]["model"] == "opus"
        assert rows[0]["notes"] == "test run"


class TestComputeDisplayKeys:
    """Regression tests for _row_id column being hidden from the table."""

    def test_row_id_excluded(self):
        keys = _compute_display_keys(
            all_keys=["precision", "_row_id", "recall"],
            preferred_keys=["precision", "recall"],
        )
        assert "_row_id" not in keys

    def test_row_id_excluded_when_only_in_extras(self):
        keys = _compute_display_keys(
            all_keys=["timestamp", "_row_id", "extra_col"],
            preferred_keys=["timestamp"],
        )
        assert keys == ["timestamp", "extra_col"]
        assert "_row_id" not in keys

    def test_preferred_keys_appear_first(self):
        keys = _compute_display_keys(
            all_keys=["zzz", "precision", "recall", "aaa"],
            preferred_keys=["precision", "recall"],
        )
        assert keys[:2] == ["precision", "recall"]

    def test_missing_preferred_keys_skipped(self):
        keys = _compute_display_keys(
            all_keys=["precision"],
            preferred_keys=["precision", "missing_key"],
        )
        assert keys == ["precision"]

    def test_no_duplicates(self):
        keys = _compute_display_keys(
            all_keys=["precision", "recall", "_row_id"],
            preferred_keys=["precision"],
        )
        assert len(keys) == len(set(keys))


class TestLoadTuningRunsMalformed:
    """Regression: corrupt CSV / encoding failures must not crash the page."""

    def test_csv_error_returns_empty(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        # Python's default csv dialect is permissive, so force an explicit
        # csv.Error by patching DictReader to raise during iteration.
        import csv as csv_mod

        csv_dir = tmp_path / "logs" / "tuning"
        csv_dir.mkdir(parents=True)
        (csv_dir / "tuning_runs.csv").write_text("timestamp,notes\n2026-01-01,ok\n")

        class BrokenReader:
            def __init__(self, *args, **kwargs):
                pass

            def __iter__(self):
                raise csv_mod.Error("forced error")

        monkeypatch.setattr(
            "pipeline_app.pages.tuning_history.csv.DictReader",
            BrokenReader,
        )
        assert _load_tuning_runs(str(tmp_path)) == []

    def test_non_utf8_bytes_return_empty(self, tmp_path: Path):
        csv_dir = tmp_path / "logs" / "tuning"
        csv_dir.mkdir(parents=True)
        (csv_dir / "tuning_runs.csv").write_bytes(b"\xff\xfe\xfd\xfc")
        assert _load_tuning_runs(str(tmp_path)) == []

    def test_malformed_csv_logs_warning(
        self,
        tmp_path: Path,
        caplog,
    ):
        csv_dir = tmp_path / "logs" / "tuning"
        csv_dir.mkdir(parents=True)
        (csv_dir / "tuning_runs.csv").write_bytes(b"\xff\xfe")
        with caplog.at_level(logging.WARNING):
            _load_tuning_runs(str(tmp_path))
        assert any(
            "Failed to read tuning_runs.csv" in r.message for r in caplog.records
        )


class TestNumericColumns:
    def test_contains_metric_columns(self):
        for col in ("precision", "recall", "f1", "f2"):
            assert col in NUMERIC_COLUMNS

    def test_contains_count_columns(self):
        for col in ("tp", "fp", "fn", "tn"):
            assert col in NUMERIC_COLUMNS

    def test_contains_threshold_columns(self):
        assert "threshold" in NUMERIC_COLUMNS
        assert "confidence_threshold" in NUMERIC_COLUMNS
        assert "f_beta_weight" in NUMERIC_COLUMNS

    def test_contains_aggregate_columns(self):
        assert "total_genes" in NUMERIC_COLUMNS
        assert "total_papers" in NUMERIC_COLUMNS
