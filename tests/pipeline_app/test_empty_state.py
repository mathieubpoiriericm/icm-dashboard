"""Tests for the empty_state component."""

from __future__ import annotations

from pipeline_app.components.empty_state import empty_state


def _class_list(element) -> list[str]:
    return list(getattr(element, "classes", []))


def _descendant_classes(root) -> list[list[str]]:
    return [_class_list(c) for c in root]


class TestEmptyStateRender:
    def test_returns_container_with_base_class(self):
        container = empty_state("inbox", "Nothing here", "Try again later.")
        assert "empty-state" in container.classes

    def test_renders_icon_heading_body_without_action(self):
        container = empty_state("inbox", "Nothing", "Body text.")
        child_classes = _descendant_classes(container)
        assert any("empty-state-icon" in c for c in child_classes)
        assert any("empty-state-heading" in c for c in child_classes)
        assert any("empty-state-body" in c for c in child_classes)
        assert not any("empty-state-action" in c for c in child_classes)

    def test_action_button_only_when_both_label_and_handler_given(self):
        container = empty_state(
            "inbox",
            "Nothing",
            "Body.",
            action_label="Go",
            on_action=lambda: None,
        )
        child_classes = _descendant_classes(container)
        assert any("empty-state-action" in c for c in child_classes)

    def test_label_without_handler_renders_no_action(self):
        container = empty_state(
            "inbox",
            "Nothing",
            "Body.",
            action_label="Go",
            on_action=None,
        )
        child_classes = _descendant_classes(container)
        assert not any("empty-state-action" in c for c in child_classes)

    def test_handler_without_label_renders_no_action(self):
        container = empty_state(
            "inbox",
            "Nothing",
            "Body.",
            action_label=None,
            on_action=lambda: None,
        )
        child_classes = _descendant_classes(container)
        assert not any("empty-state-action" in c for c in child_classes)
