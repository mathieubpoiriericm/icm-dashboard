"""Tests for filesystem navigation helpers (is_within, list_directory)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pipeline_app.components.fs_nav import (
    is_within,
    list_directory,
    scan_directory,
)


class TestIsWithin:
    def test_child_inside_anchor(self, tmp_path: Path):
        assert is_within(tmp_path / "a", tmp_path.resolve())

    def test_sibling_outside_anchor(self, tmp_path: Path):
        sibling = tmp_path.parent / "outside"
        assert not is_within(sibling, tmp_path.resolve())

    def test_symlink_escape_rejected(self, tmp_path: Path):
        outside = tmp_path.parent / "fs_nav_outside"
        outside.mkdir(exist_ok=True)
        try:
            link = tmp_path / "escape"
            link.symlink_to(outside)
            assert not is_within(link, tmp_path.resolve())
        finally:
            if outside.exists():
                for f in outside.iterdir():
                    f.unlink()
                outside.rmdir()


class TestListDirectory:
    def test_returns_empty_for_empty_dir(self, tmp_path: Path):
        assert list_directory(tmp_path) == []

    def test_returns_all_files_when_no_extension_filter(self, tmp_path: Path):
        (tmp_path / "a.pdf").write_text("")
        (tmp_path / "b.py").write_text("")
        (tmp_path / "c.txt").write_text("")
        entries = list_directory(tmp_path)
        assert [e.path.name for e in entries] == ["a.pdf", "b.py", "c.txt"]
        assert all(e.matches_filter for e in entries)

    def test_extensions_filter_marks_non_matching(self, tmp_path: Path):
        (tmp_path / "a.pdf").write_text("")
        (tmp_path / "b.txt").write_text("")
        entries = list_directory(tmp_path, extensions=frozenset({".pdf"}))
        by_name = {e.path.name: e for e in entries}
        assert by_name["a.pdf"].matches_filter is True
        assert by_name["b.txt"].matches_filter is False

    def test_extension_filter_is_case_insensitive(self, tmp_path: Path):
        (tmp_path / "report.PDF").write_text("")
        entries = list_directory(tmp_path, extensions=frozenset({".pdf"}))
        assert len(entries) == 1
        assert entries[0].matches_filter is True

    def test_directories_always_included(self, tmp_path: Path):
        (tmp_path / "subdir").mkdir()
        (tmp_path / "irrelevant.py").write_text("")
        entries = list_directory(tmp_path, extensions=frozenset({".pdf"}))
        dirs = [e for e in entries if e.is_dir]
        assert len(dirs) == 1
        assert dirs[0].path.name == "subdir"

    def test_directories_sorted_before_files(self, tmp_path: Path):
        (tmp_path / "z_file.txt").write_text("")
        (tmp_path / "a_dir").mkdir()
        entries = list_directory(tmp_path)
        assert entries[0].is_dir
        assert entries[0].path.name == "a_dir"
        assert entries[1].path.name == "z_file.txt"

    def test_symlinks_rejected_by_default(self, tmp_path: Path):
        real = tmp_path / "real.pdf"
        real.write_text("")
        link = tmp_path / "link.pdf"
        link.symlink_to(real)
        entries = list_directory(tmp_path)
        names = {e.path.name for e in entries}
        assert "real.pdf" in names
        assert "link.pdf" not in names

    def test_symlinks_preserved_when_requested(self, tmp_path: Path):
        real = tmp_path / "real_bin"
        real.write_text("")
        link = tmp_path / "python3.12"
        link.symlink_to(real)
        entries = list_directory(tmp_path, symlinks="preserve")
        names = {e.path.name for e in entries}
        assert "python3.12" in names
        assert "real_bin" in names

    def test_empty_directories_visible(self, tmp_path: Path):
        (tmp_path / "empty").mkdir()
        entries = list_directory(tmp_path)
        assert len(entries) == 1
        assert entries[0].is_dir

    def test_unicode_paths(self, tmp_path: Path):
        unicode_dir = tmp_path / "日本語"
        unicode_dir.mkdir()
        (unicode_dir / "ファイル.pdf").write_text("")
        entries = list_directory(unicode_dir, extensions=frozenset({".pdf"}))
        assert len(entries) == 1
        assert entries[0].path.name == "ファイル.pdf"

    def test_returns_empty_on_permission_error(self, tmp_path: Path):
        if os.name == "nt" or os.getuid() == 0:
            pytest.skip("chmod not effective in this environment")
        restricted = tmp_path / "locked"
        restricted.mkdir()
        restricted.chmod(0o000)
        try:
            assert list_directory(restricted) == []
        finally:
            restricted.chmod(0o755)

    def test_returns_empty_for_file_not_dir(self, tmp_path: Path):
        (tmp_path / "a.txt").write_text("")
        assert list_directory(tmp_path / "a.txt") == []


class TestScanDirectoryBackCompat:
    """Verify scan_directory (used by file_browser.py) still works as before."""

    def test_recursive_with_supported_extensions(self, tmp_path: Path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "report.json").write_text("{}")
        nodes = scan_directory(tmp_path, tmp_path)
        assert len(nodes) == 1
        assert nodes[0]["label"] == "sub"
        assert nodes[0]["children"][0]["label"] == "report.json"

    def test_empty_dirs_still_excluded_by_default(self, tmp_path: Path):
        (tmp_path / "empty").mkdir()
        assert scan_directory(tmp_path, tmp_path) == []
