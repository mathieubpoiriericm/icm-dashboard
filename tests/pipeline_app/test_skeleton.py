"""Tests for skeleton loader primitives."""

from __future__ import annotations

import pytest
from pipeline_app.components.skeleton import (
    skeleton_card,
    skeleton_stat_grid,
    skeleton_table,
)


def _walk(element):
    """Yield all descendants of ``element`` depth-first."""
    for child in element:
        yield child
        yield from _walk(child)


class TestSkeletonCard:
    def test_has_base_class(self):
        card = skeleton_card()
        assert "skeleton-card" in card.classes

    def test_emits_title_row_plus_rows(self):
        card = skeleton_card(rows=3)
        # 1 title + 3 body rows = 4 descendant divs with .skeleton-row
        rows = [
            c
            for c in _walk(card)
            if any(cls == "skeleton-row" for cls in getattr(c, "classes", []))
        ]
        assert len(rows) == 4

    @pytest.mark.parametrize("n, expected_body", [(0, 1), (-5, 1), (1, 1)])
    def test_rows_clamped_to_at_least_one(self, n: int, expected_body: int):
        card = skeleton_card(rows=n)
        rows = [
            c
            for c in _walk(card)
            if any(cls == "skeleton-row" for cls in getattr(c, "classes", []))
        ]
        # Always 1 title + at least expected_body body rows
        assert len(rows) == 1 + expected_body


class TestSkeletonTable:
    def test_has_base_class(self):
        table = skeleton_table()
        assert "skeleton-table" in table.classes

    def test_row_count_includes_header(self):
        table = skeleton_table(rows=4, cols=3)
        table_rows = [
            c for c in table if "skeleton-table-row" in getattr(c, "classes", [])
        ]
        assert len(table_rows) == 5  # 1 header + 4 body

    def test_cells_per_row(self):
        table = skeleton_table(rows=2, cols=5)
        for row in table:
            cells = [
                c for c in row if "skeleton-table-cell" in getattr(c, "classes", [])
            ]
            assert len(cells) == 5

    @pytest.mark.parametrize("rows, cols", [(0, 0), (-3, -3)])
    def test_clamps_to_at_least_one(self, rows: int, cols: int):
        table = skeleton_table(rows=rows, cols=cols)
        table_rows = [
            c for c in table if "skeleton-table-row" in getattr(c, "classes", [])
        ]
        assert len(table_rows) == 2  # header + 1 body
        for row in table_rows:
            cells = [
                c for c in row if "skeleton-table-cell" in getattr(c, "classes", [])
            ]
            assert len(cells) == 1


class TestSkeletonStatGrid:
    def test_has_base_class(self):
        grid = skeleton_stat_grid()
        assert "skeleton-stat-grid" in grid.classes

    def test_item_count_matches(self):
        grid = skeleton_stat_grid(count=6)
        items = [c for c in grid if "skeleton-stat-item" in getattr(c, "classes", [])]
        assert len(items) == 6

    @pytest.mark.parametrize("n", [0, -1])
    def test_count_clamped_to_at_least_one(self, n: int):
        grid = skeleton_stat_grid(count=n)
        items = [c for c in grid if "skeleton-stat-item" in getattr(c, "classes", [])]
        assert len(items) == 1
