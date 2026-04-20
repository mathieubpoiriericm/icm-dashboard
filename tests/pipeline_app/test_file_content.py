"""Tests for file content type detection."""

from __future__ import annotations

from pipeline_app.components.file_content import (
    MAX_BINARY_FILE_SIZE,
    MAX_FILE_SIZE,
    SUPPORTED_EXTENSIONS,
    detect_file_type,
)


class TestDetectFileType:
    def test_json(self):
        assert detect_file_type("report.json") == "json"

    def test_csv(self):
        assert detect_file_type("data.csv") == "csv"

    def test_png(self):
        assert detect_file_type("plot.png") == "image"

    def test_jpg(self):
        assert detect_file_type("photo.jpg") == "image"

    def test_pdf(self):
        assert detect_file_type("document.pdf") == "pdf"

    def test_markdown(self):
        assert detect_file_type("readme.md") == "text"

    def test_log(self):
        assert detect_file_type("pipeline.log") == "text"

    def test_txt(self):
        assert detect_file_type("notes.txt") == "text"

    def test_unknown_returns_none(self):
        assert detect_file_type("binary.exe") is None

    def test_jpeg(self):
        assert detect_file_type("photo.jpeg") == "image"

    def test_gif(self):
        assert detect_file_type("animation.gif") == "image"

    def test_case_insensitive(self):
        assert detect_file_type("REPORT.JSON") == "json"

    def test_nested_path(self):
        assert detect_file_type("logs/json/report.json") == "json"


class TestSupportedExtensions:
    def test_includes_expected(self):
        for ext in (
            ".json",
            ".csv",
            ".png",
            ".pdf",
            ".md",
            ".txt",
            ".log",
        ):
            assert ext in SUPPORTED_EXTENSIONS


class TestFileSizeLimits:
    def test_binary_limit_lower_than_text(self):
        assert MAX_BINARY_FILE_SIZE < MAX_FILE_SIZE

    def test_binary_limit_is_10mb(self):
        assert MAX_BINARY_FILE_SIZE == 10 * 1024 * 1024

    def test_text_limit_is_50mb(self):
        assert MAX_FILE_SIZE == 50 * 1024 * 1024
