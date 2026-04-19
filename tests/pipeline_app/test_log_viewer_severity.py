"""Tests for the log_viewer severity detection."""

from __future__ import annotations

import pytest
from pipeline_app.components.log_viewer import detect_severity


class TestDetectSeverityErrors:
    @pytest.mark.parametrize(
        "line",
        [
            "ERROR: connection refused",
            "2026-04-19 10:00:00 ERROR something broke",
            "[ERROR] formatted logger",
            "critical failure",
            "FATAL: unrecoverable",
            "Traceback (most recent call last):",
        ],
    )
    def test_error_lines_detected(self, line: str):
        assert detect_severity(line) == "error"

    def test_error_case_insensitive(self):
        assert detect_severity("error: lower case") == "error"
        assert detect_severity("Error: title case") == "error"


class TestDetectSeverityWarnings:
    @pytest.mark.parametrize(
        "line",
        [
            "WARNING: rate limited",
            "WARN: degraded",
            "[WARN] formatted",
            "warning: lowercase",
        ],
    )
    def test_warn_lines_detected(self, line: str):
        assert detect_severity(line) == "warn"


class TestDetectSeverityDebug:
    @pytest.mark.parametrize(
        "line",
        ["DEBUG: trace", "[DEBUG] formatted", "debug: value=42"],
    )
    def test_debug_lines_detected(self, line: str):
        assert detect_severity(line) == "debug"


class TestDetectSeverityDefault:
    @pytest.mark.parametrize(
        "line",
        [
            "INFO: processing",
            "Connected to database",
            "Processed 42 papers",
            "",
            "some plain text",
        ],
    )
    def test_info_default(self, line: str):
        assert detect_severity(line) == "info"


class TestSeverityPriority:
    def test_error_beats_warn_when_both_present(self):
        # Tracebacks often reference warn/debug context; should still be error.
        assert detect_severity("ERROR processing WARN message") == "error"

    def test_error_beats_debug(self):
        assert detect_severity("DEBUG: got ERROR from upstream") == "error"

    def test_warn_beats_debug(self):
        assert detect_severity("DEBUG: WARN downgrade") == "warn"
