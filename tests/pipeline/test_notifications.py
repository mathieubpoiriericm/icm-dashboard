import logging
from unittest.mock import MagicMock, patch

import pytest

from pipeline.config import PipelineConfig
from pipeline.notifications import (
    _build_template_context,
    _format_duration,
    _render_html,
    _render_markdown,
    send_pipeline_notification,
)

# ---------------------------------------------------------------------------
# Test data helper
# ---------------------------------------------------------------------------


def _make_run_data(
    *,
    mode=None,
    dry_run=False,
    papers_detail=None,
    database=None,
    batch_warnings=None,
):
    """Return a minimal PipelineRunData dict."""
    cfg = {
        "model": "claude-opus-4-6",
        "effort": "high",
    }
    if mode is not None:
        cfg["mode"] = mode
    if dry_run:
        cfg["dry_run"] = True
    if mode is None:
        cfg["days_back"] = 7
    if mode == "local_pdf":
        cfg["pdf_directory"] = "/tmp/pdfs"
    if mode == "pmid_list":
        cfg["pmid_file"] = "/tmp/pmids.txt"

    return {
        "timestamp": "2026-02-14T00:00:00+00:00",
        "total_processing_time": 42.5,
        "total_compute_time": 30.0,
        "pipeline_config": cfg,
        "search": {"pmids_found": 10, "pmids_new": 5, "pmids_skipped": 5},
        "papers": {
            "processed": 5,
            "fulltext": 3,
            "abstract_only": 2,
            "fulltext_rate": 0.6,
            "failed": 0,
        },
        "genes": {
            "extracted": 8,
            "validated": 7,
            "rejected": 1,
            "acceptance_rate": 0.875,
        },
        "token_usage": {
            "input_tokens": 1000,
            "output_tokens": 500,
            "cache_creation_input_tokens": 200,
            "cache_read_input_tokens": 100,
            "total_tokens": 1800,
            "cache_hit_rate": 0.5,
            "estimated_cost_usd": 0.05,
        },
        "database": database,
        "batch_validation_warnings": batch_warnings or [],
        "papers_detail": papers_detail or [],
    }


def _make_config(**overrides):
    """Return a PipelineConfig with notify_urls populated."""
    config = PipelineConfig()
    config.notify_urls = overrides.get("notify_urls", "json://stdout")
    config.notify_max_retries = overrides.get("notify_max_retries", 1)
    config.notify_retry_min_wait = overrides.get("notify_retry_min_wait", 0.1)
    config.notify_retry_max_wait = overrides.get("notify_retry_max_wait", 0.2)
    return config


# ---------------------------------------------------------------------------
# Tests: send_pipeline_notification
# ---------------------------------------------------------------------------


def test_send_notification_no_urls(caplog):
    """Return early with warning when notify_urls is empty."""
    config = PipelineConfig()
    config.notify_urls = ""

    with caplog.at_level(logging.WARNING):
        send_pipeline_notification(_make_run_data(), config)

    assert "Notification URLs not configured" in caplog.text


@patch("pipeline.notifications.apprise.Apprise")
def test_send_notification_success(mock_apprise_cls, caplog):
    """Successfully send via Apprise."""
    mock_instance = MagicMock()
    mock_instance.notify.return_value = True
    mock_apprise_cls.return_value = mock_instance

    config = _make_config()
    run_data = _make_run_data()

    with caplog.at_level(logging.INFO):
        send_pipeline_notification(run_data, config)

    assert "Pipeline notification sent successfully" in caplog.text
    mock_instance.add.assert_called()
    mock_instance.notify.assert_called_once()


@patch("pipeline.notifications.apprise.Apprise")
def test_send_notification_failure_graceful(mock_apprise_cls, caplog):
    """Apprise error is logged but does not propagate."""
    mock_instance = MagicMock()
    mock_instance.notify.side_effect = RuntimeError("connection failed")
    mock_apprise_cls.return_value = mock_instance

    config = _make_config()

    with caplog.at_level(logging.ERROR):
        send_pipeline_notification(_make_run_data(), config)

    assert "Failed to send pipeline notification" in caplog.text


# ---------------------------------------------------------------------------
# Tests: template rendering
# ---------------------------------------------------------------------------


def test_render_html_standard():
    """HTML rendering includes key sections for standard mode."""
    run_data = _make_run_data(database={"inserted": 3, "updated": 1})
    html = _render_html(run_data)
    assert "Run Overview" in html
    assert "Search Results" in html
    assert "Papers" in html
    assert "Genes" in html
    assert "Database" in html
    assert "Inserted" in html


def test_render_html_local_pdf_excludes_search():
    """Local PDF mode excludes Search and Database sections."""
    html = _render_html(_make_run_data(mode="local_pdf"))
    assert "Local PDF" in html
    assert "Search Results" not in html
    assert "Database" not in html


def test_render_html_dryrun_excludes_database():
    """Dry run mode excludes Database section."""
    html = _render_html(_make_run_data(dry_run=True))
    assert "Dry Run" in html
    assert ">Database<" not in html


def test_render_html_batch_warnings():
    """Batch warnings appear in the HTML body."""
    html = _render_html(_make_run_data(batch_warnings=["High gene duplication"]))
    assert "High gene duplication" in html


def test_render_html_missing_fulltext():
    """Missing fulltext papers appear in the HTML body."""
    papers = [
        {
            "pmid": "123",
            "fulltext": False,
            "source": "none",
            "error": "No text",
            "gene_count": 0,
            "genes": [],
            "processing_time": 1.0,
        },
    ]
    html = _render_html(_make_run_data(papers_detail=papers))
    assert "Missing Full-Text" in html
    assert "123" in html


def test_render_markdown_standard():
    """Markdown rendering includes key metrics."""
    md = _render_markdown(_make_run_data())
    assert "SVD Pipeline Standard" in md
    assert "Papers:" in md
    assert "Genes:" in md


def test_render_markdown_local_pdf():
    """Markdown rendering handles local PDF mode."""
    md = _render_markdown(_make_run_data(mode="local_pdf"))
    assert "Local PDF" in md
    assert "Search:" not in md


# ---------------------------------------------------------------------------
# Tests: _build_template_context
# ---------------------------------------------------------------------------


def test_template_context_standard():
    """Standard mode context has expected fields."""
    ctx = _build_template_context(_make_run_data())
    assert ctx["mode_label"] == "Standard"
    assert ctx["show_search"] is True
    assert ctx["show_database"] is False  # no database in default run_data


def test_template_context_standard_with_db():
    """Standard mode with database results shows database."""
    ctx = _build_template_context(
        _make_run_data(database={"inserted": 2, "updated": 0})
    )
    assert ctx["show_database"] is True


def test_template_context_dryrun():
    """Dry run mode label and no database."""
    ctx = _build_template_context(_make_run_data(dry_run=True))
    assert ctx["mode_label"] == "Standard (Dry Run)"
    assert ctx["show_database"] is False


# ---------------------------------------------------------------------------
# Tests: _format_duration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (5.3, "5.3s"),
        (0.0, "0.0s"),
        (59.9, "59.9s"),
        (60.0, "1m 0s"),
        (125.0, "2m 5s"),
        (3661.0, "1h 1m 1s"),
        (7200.0, "2h 0m 0s"),
    ],
    ids=[
        "short",
        "zero",
        "under-minute",
        "exact-minute",
        "minutes",
        "hours",
        "even-hours",
    ],
)
def test_format_duration(seconds, expected):
    assert _format_duration(seconds) == expected
