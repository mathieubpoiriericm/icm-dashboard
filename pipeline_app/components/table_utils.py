"""Table construction helpers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TypedDict


class TableColumn(TypedDict, total=False):
    """NiceGUI/Quasar table column configuration."""

    name: str
    label: str
    field: str
    sortable: bool


def table_columns(
    specs: Iterable[tuple[str, str] | tuple[str, str, bool]],
) -> list[TableColumn]:
    """Build standard column dictionaries from compact specs."""
    columns: list[TableColumn] = []
    for spec in specs:
        name, label, *rest = spec
        sortable = rest[0] if rest else True
        col: TableColumn = {"name": name, "label": label, "field": name}
        if sortable:
            col["sortable"] = True
        columns.append(col)
    return columns
