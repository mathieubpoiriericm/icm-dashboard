"""Tests for file browser directory scanning and path validation."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pipeline_app.components.fs_nav import scan_directory


class TestScanDirectory:
    def test_returns_supported_files(self, tmp_path: Path):
        (tmp_path / "report.json").write_text("{}")
        (tmp_path / "data.csv").write_text("a,b")
        (tmp_path / "notes.txt").write_text("hello")
        nodes = scan_directory(tmp_path, tmp_path)
        labels = {n["label"] for n in nodes}
        assert labels == {"report.json", "data.csv", "notes.txt"}

    def test_excludes_unsupported_extensions(self, tmp_path: Path):
        (tmp_path / "script.py").write_text("pass")
        (tmp_path / "data.parquet").write_bytes(b"\x00")
        nodes = scan_directory(tmp_path, tmp_path)
        assert nodes == []

    def test_skips_symlinks(self, tmp_path: Path):
        real_file = tmp_path / "real.json"
        real_file.write_text("{}")
        link = tmp_path / "link.json"
        link.symlink_to(real_file)
        nodes = scan_directory(tmp_path, tmp_path)
        labels = {n["label"] for n in nodes}
        assert "real.json" in labels
        assert "link.json" not in labels

    def test_rejects_path_outside_root(self, tmp_path: Path):
        # Create a directory structure where a symlinked dir escapes root
        root = tmp_path / "project"
        root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.json").write_text("{}")

        # Create a symlink inside root that points outside
        escape_link = root / "escape"
        escape_link.symlink_to(outside)

        nodes = scan_directory(root, root)
        # The symlink directory should be skipped
        assert nodes == []

    def test_recurses_into_subdirectories(self, tmp_path: Path):
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "nested.json").write_text("{}")
        nodes = scan_directory(tmp_path, tmp_path)
        assert len(nodes) == 1
        assert nodes[0]["label"] == "subdir"
        assert len(nodes[0]["children"]) == 1
        assert nodes[0]["children"][0]["label"] == "nested.json"

    def test_excludes_empty_directories(self, tmp_path: Path):
        (tmp_path / "empty_dir").mkdir()
        nodes = scan_directory(tmp_path, tmp_path)
        assert nodes == []

    def test_directories_sorted_before_files(self, tmp_path: Path):
        (tmp_path / "z_file.json").write_text("{}")
        sub = tmp_path / "a_dir"
        sub.mkdir()
        (sub / "nested.json").write_text("{}")
        nodes = scan_directory(tmp_path, tmp_path)
        assert len(nodes) == 2
        assert nodes[0]["label"] == "a_dir"
        assert nodes[1]["label"] == "z_file.json"

    @pytest.mark.skipif(os.name == "nt", reason="chmod not effective on Windows")
    @pytest.mark.skipif(
        os.name != "nt" and os.getuid() == 0,
        reason="chmod has no effect as root",
    )
    def test_handles_permission_error(self, tmp_path: Path):
        restricted = tmp_path / "restricted"
        restricted.mkdir()
        (restricted / "file.json").write_text("{}")
        restricted.chmod(0o000)
        try:
            nodes = scan_directory(tmp_path, tmp_path)
            # restricted directory should be skipped gracefully
            assert not any(n["label"] == "restricted" for n in nodes)
        finally:
            restricted.chmod(0o755)

    def test_uses_provided_resolved_root(self, tmp_path: Path):
        (tmp_path / "data.json").write_text("{}")
        resolved = tmp_path.resolve()
        nodes = scan_directory(tmp_path, tmp_path, resolved_root=resolved)
        labels = {n["label"] for n in nodes}
        assert "data.json" in labels
