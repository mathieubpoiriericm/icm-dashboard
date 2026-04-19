"""Tests for path picker pure helpers and selection logic."""

from __future__ import annotations

from pathlib import Path

import pytest
from pipeline_app.components.path_picker import (
    _breadcrumb_segments,
    _compute_selection,
    combine_save_as,
    is_valid_save_as_filename,
    resolve_start_dir,
)


class TestResolveStartDir:
    def test_existing_directory_from_current_value(self, tmp_path: Path):
        assert resolve_start_dir(str(tmp_path), None, None) == tmp_path.resolve()

    def test_existing_file_uses_parent(self, tmp_path: Path):
        f = tmp_path / "a.pdf"
        f.write_text("")
        assert resolve_start_dir(str(f), None, None) == tmp_path.resolve()

    def test_empty_current_value_uses_fallback(self, tmp_path: Path):
        assert resolve_start_dir("", tmp_path, None) == tmp_path.resolve()

    def test_bare_name_like_python3_uses_fallback(self, tmp_path: Path):
        assert resolve_start_dir("python3", tmp_path, None) == tmp_path.resolve()

    def test_falls_back_to_anchor_when_no_fallback(self, tmp_path: Path):
        assert resolve_start_dir("", None, tmp_path) == tmp_path.resolve()

    def test_falls_back_to_home_as_last_resort(self):
        result = resolve_start_dir("", None, None)
        assert result == Path.home().resolve()

    def test_never_returns_filesystem_root(self):
        result = resolve_start_dir("/", Path("/"), Path("/"))
        assert result == Path.home().resolve()

    def test_nonexistent_current_value_skipped(self, tmp_path: Path):
        bogus = tmp_path / "does-not-exist-here"
        assert resolve_start_dir(str(bogus), tmp_path, None) == tmp_path.resolve()


class TestIsValidSaveAsFilename:
    @pytest.mark.parametrize(
        "name", ["progress.json", "file_1.txt", "my-report.csv", "a.b.c"]
    )
    def test_accepts_normal_names(self, name: str):
        assert is_valid_save_as_filename(name)

    @pytest.mark.parametrize("name", ["", "   ", "."])
    def test_rejects_empty_or_dot(self, name: str):
        assert not is_valid_save_as_filename(name)

    def test_rejects_parent_traversal(self):
        assert not is_valid_save_as_filename("..")

    @pytest.mark.parametrize(
        "name", ["a/b.txt", "a\\b.txt", "x\x00y", "/abs/file.txt"]
    )
    def test_rejects_separator_or_null(self, name: str):
        assert not is_valid_save_as_filename(name)

    def test_rejects_oversized_name(self):
        assert not is_valid_save_as_filename("a" * 256)

    def test_strips_surrounding_whitespace(self):
        assert is_valid_save_as_filename("  ok.json  ")


class TestCombineSaveAs:
    def test_joins_directory_and_filename(self, tmp_path: Path):
        result = combine_save_as(tmp_path, "progress.json", tmp_path.resolve())
        assert result == tmp_path / "progress.json"

    def test_rejects_invalid_filename(self, tmp_path: Path):
        assert combine_save_as(tmp_path, "a/b.txt", tmp_path.resolve()) is None

    def test_enforces_anchor(self, tmp_path: Path):
        inside = tmp_path / "inside"
        inside.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        # Providing a directory outside the anchor must be rejected.
        assert combine_save_as(outside, "a.json", inside.resolve()) is None

    def test_passes_when_anchor_none(self, tmp_path: Path):
        assert (
            combine_save_as(tmp_path, "progress.json", None)
            == tmp_path / "progress.json"
        )

    def test_strips_whitespace_from_filename(self, tmp_path: Path):
        result = combine_save_as(tmp_path, "  ok.json  ", None)
        assert result == tmp_path / "ok.json"


class TestComputeSelection:
    def test_directory_mode_returns_current_dir(self, tmp_path: Path):
        result = _compute_selection(
            mode="directory",
            current_dir=tmp_path,
            selected_file=None,
            anchor=tmp_path.resolve(),
            save_as=False,
            filename="",
        )
        assert result == tmp_path

    def test_directory_mode_rejects_outside_anchor(self, tmp_path: Path):
        inside = tmp_path / "in"
        inside.mkdir()
        outside = tmp_path / "out"
        outside.mkdir()
        assert (
            _compute_selection(
                mode="directory",
                current_dir=outside,
                selected_file=None,
                anchor=inside.resolve(),
                save_as=False,
                filename="",
            )
            is None
        )

    def test_file_mode_returns_selected_file(self, tmp_path: Path):
        f = tmp_path / "a.pdf"
        f.write_text("")
        result = _compute_selection(
            mode="file",
            current_dir=tmp_path,
            selected_file=f,
            anchor=tmp_path.resolve(),
            save_as=False,
            filename="",
        )
        assert result == f

    def test_file_mode_returns_none_when_no_file_selected(self, tmp_path: Path):
        assert (
            _compute_selection(
                mode="file",
                current_dir=tmp_path,
                selected_file=None,
                anchor=None,
                save_as=False,
                filename="",
            )
            is None
        )

    def test_file_mode_rejects_file_outside_anchor(self, tmp_path: Path):
        inside = tmp_path / "in"
        inside.mkdir()
        outside_file = tmp_path / "escape.pdf"
        outside_file.write_text("")
        assert (
            _compute_selection(
                mode="file",
                current_dir=inside,
                selected_file=outside_file,
                anchor=inside.resolve(),
                save_as=False,
                filename="",
            )
            is None
        )

    def test_save_as_combines_dir_and_filename(self, tmp_path: Path):
        result = _compute_selection(
            mode="directory",
            current_dir=tmp_path,
            selected_file=None,
            anchor=tmp_path.resolve(),
            save_as=True,
            filename="progress.json",
        )
        assert result == tmp_path / "progress.json"

    def test_save_as_rejects_invalid_filename(self, tmp_path: Path):
        assert (
            _compute_selection(
                mode="directory",
                current_dir=tmp_path,
                selected_file=None,
                anchor=tmp_path.resolve(),
                save_as=True,
                filename="a/b.json",
            )
            is None
        )

    def test_picked_file_deleted_externally_still_returned(self, tmp_path: Path):
        f = tmp_path / "gone.pdf"
        f.write_text("")
        picked = f
        f.unlink()
        # Picker is not the validation boundary; submit-time validators
        # reject missing paths. Returning the raw selection is expected.
        result = _compute_selection(
            mode="file",
            current_dir=tmp_path,
            selected_file=picked,
            anchor=tmp_path.resolve(),
            save_as=False,
            filename="",
        )
        assert result == picked


class TestBreadcrumbSegments:
    def test_unanchored_walks_to_root(self, tmp_path: Path):
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        chain = _breadcrumb_segments(deep, None)
        # The deepest path in the chain must be our current directory.
        assert chain[-1] == deep
        # And the chain must reach the filesystem root (unanchored case).
        assert chain[0] == Path(chain[0].anchor)

    def test_stops_at_anchor(self, tmp_path: Path):
        anchor = tmp_path / "root"
        deep = anchor / "sub" / "leaf"
        deep.mkdir(parents=True)
        chain = _breadcrumb_segments(deep, anchor)
        assert chain[0] == anchor.resolve() or chain[0] == anchor
        assert deep in chain

    def test_current_dir_is_last_segment(self, tmp_path: Path):
        chain = _breadcrumb_segments(tmp_path, None)
        assert chain[-1] == tmp_path
