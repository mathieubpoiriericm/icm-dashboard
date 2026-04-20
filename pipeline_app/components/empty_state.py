"""Empty state primitive — icon + heading + body + optional CTA."""

from __future__ import annotations

from collections.abc import Callable

from nicegui import ui


def empty_state(
    icon: str,
    heading: str,
    body: str,
    *,
    action_label: str | None = None,
    on_action: Callable[[], None] | None = None,
) -> ui.element:
    """Render a centered empty-state panel.

    Use on history/results surfaces when a query returns no rows, instead
    of a bare "No data" label. The CTA is only rendered when both
    ``action_label`` and ``on_action`` are supplied.

    Args:
        icon: Material icon name for the hero glyph.
        heading: Short primary line (rendered in the display face).
        body: Supporting sentence explaining what would appear here.
        action_label: Optional CTA button label.
        on_action: Optional CTA button click handler.

    Returns:
        The wrapping container so callers can further position it.
    """
    container = ui.element("div").classes("empty-state w-full")
    with container:
        ui.icon(icon).classes("empty-state-icon")
        ui.label(heading).classes("empty-state-heading")
        ui.label(body).classes("empty-state-body")
        if action_label and on_action is not None:
            ui.button(
                action_label,
                on_click=on_action,
            ).classes("empty-state-action btn-execute").props("unelevated")
    return container
