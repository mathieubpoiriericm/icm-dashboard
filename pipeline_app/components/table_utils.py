"""Table construction helpers."""

from __future__ import annotations

from collections.abc import Iterable


def table_columns(
    specs: Iterable[tuple[str, str] | tuple[str, str, bool]],
) -> list[dict[str, object]]:
    """Build standard column dictionaries from compact specs."""
    columns: list[dict[str, object]] = []
    for spec in specs:
        name, label, *rest = spec
        sortable = rest[0] if rest else True
        col: dict[str, object] = {"name": name, "label": label, "field": name}
        if sortable:
            col["sortable"] = True
        columns.append(col)
    return columns
