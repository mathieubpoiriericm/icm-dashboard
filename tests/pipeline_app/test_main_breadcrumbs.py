"""Tests for the header breadcrumb helper."""

from __future__ import annotations

from pipeline_app.main import _breadcrumbs_for


class TestBreadcrumbsFor:
    def test_root_is_configure_run(self):
        assert _breadcrumbs_for("/") == (("Configure & Run", None),)

    def test_history_is_run_history(self):
        assert _breadcrumbs_for("/history") == (("Run History", None),)

    def test_nested_tuning_history_has_two_crumbs(self):
        chain = _breadcrumbs_for("/tuning/history")
        assert chain == (("Tuning", "/tuning"), ("History", None))

    def test_unknown_path_returns_empty(self):
        assert _breadcrumbs_for("/nope") == ()

    def test_trailing_replaces_trailing_none_crumb(self):
        chain = _breadcrumbs_for("/results", trailing="Report · abc123")
        assert chain == (
            ("Run History", "/history"),
            ("Report · abc123", None),
        )

    def test_trailing_appends_when_last_has_link(self):
        # /tuning has a single trailing (Tuning, None). A trailing replaces
        # that. But what if last had a link? Build a synthetic case by
        # passing trailing onto a path whose last crumb already is a link.
        # The current mapping has no such path; we simulate by feeding an
        # unknown path with a trailing — result should still have just the
        # trailing crumb since the chain is empty.
        chain = _breadcrumbs_for("/does-not-exist", trailing="End")
        assert chain == (("End", None),)

    def test_trailing_none_returns_chain_unchanged(self):
        assert _breadcrumbs_for("/tuning", trailing=None) == (("Tuning", None),)
