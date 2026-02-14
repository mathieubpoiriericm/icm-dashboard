import logging
import smtplib
from unittest.mock import patch

import pytest

from pipeline.config import PipelineConfig
from pipeline.notifications import (
    _format_duration,
    _parse_recipients,
    send_pipeline_summary_email,
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
    """Return a PipelineConfig with email settings populated."""
    config = PipelineConfig()
    config.email_host = overrides.get("email_host", "smtp.example.com")
    config.email_port = overrides.get("email_port", 587)
    config.email_user = overrides.get("email_user", "user")
    config.email_password = overrides.get("email_password", "password")
    config.email_from = overrides.get("email_from", "sender@example.com")
    config.email_admin = overrides.get("email_admin", "admin@example.com")
    return config


# ---------------------------------------------------------------------------
# Tests: send_pipeline_summary_email
# ---------------------------------------------------------------------------


def test_send_summary_email_no_config(caplog):
    """Return early with warning when email host is not configured."""
    config = PipelineConfig()
    config.email_host = ""

    with caplog.at_level(logging.WARNING):
        send_pipeline_summary_email(_make_run_data(), config)

    assert "Email configuration missing" in caplog.text


def test_no_recipients(caplog):
    """Return early with warning when email_admin is empty."""
    config = _make_config(email_admin="")

    with caplog.at_level(logging.WARNING):
        send_pipeline_summary_email(_make_run_data(), config)

    assert "No valid email recipients" in caplog.text


@patch("pipeline.notifications.smtplib.SMTP")
def test_send_summary_email_success_port587(mock_smtp, caplog):
    """Successfully send via port 587 with STARTTLS."""
    config = _make_config()
    run_data = _make_run_data(
        papers_detail=[
            {
                "pmid": "123",
                "fulltext": False,
                "source": "none",
                "error": "No text",
                "gene_count": 0,
                "genes": [],
                "processing_time": 1.0,
            },
        ],
    )

    with caplog.at_level(logging.INFO):
        send_pipeline_summary_email(run_data, config)

    assert "Pipeline summary email sent" in caplog.text

    mock_smtp.assert_called_with("smtp.example.com", 587)
    instance = mock_smtp.return_value
    instance.starttls.assert_called()
    instance.login.assert_called_with("user", "password")
    instance.sendmail.assert_called_once()

    # Verify sendmail args
    call_args = instance.sendmail.call_args
    assert call_args[0][0] == "sender@example.com"
    assert call_args[0][1] == ["admin@example.com"]
    body = call_args[0][2]
    assert "Missing Full-Text" in body
    assert "123" in body


@patch("pipeline.notifications.smtplib.SMTP_SSL")
def test_port465_uses_smtp_ssl(mock_smtp_ssl):
    """Port 465 uses SMTP_SSL (implicit TLS), not plain SMTP."""
    config = _make_config(email_port=465)

    send_pipeline_summary_email(_make_run_data(), config)

    mock_smtp_ssl.assert_called_once()
    call_args = mock_smtp_ssl.call_args
    assert call_args[0][0] == "smtp.example.com"
    assert call_args[0][1] == 465


@patch("pipeline.notifications.smtplib.SMTP")
def test_port25_no_tls(mock_smtp):
    """Port 25 uses plain SMTP without STARTTLS."""
    config = _make_config(email_port=25, email_user="", email_password="")

    send_pipeline_summary_email(_make_run_data(), config)

    mock_smtp.assert_called_with("smtp.example.com", 25)
    instance = mock_smtp.return_value
    instance.starttls.assert_not_called()
    instance.login.assert_not_called()
    instance.sendmail.assert_called_once()


@patch("pipeline.notifications.smtplib.SMTP")
def test_multiple_recipients(mock_smtp):
    """sendmail receives a list of all recipients; To header lists both."""
    config = _make_config(email_admin="alice@x.com, bob@y.com")

    send_pipeline_summary_email(_make_run_data(), config)

    instance = mock_smtp.return_value
    call_args = instance.sendmail.call_args
    assert call_args[0][1] == ["alice@x.com", "bob@y.com"]
    body = call_args[0][2]
    assert "alice@x.com" in body
    assert "bob@y.com" in body


@patch("pipeline.notifications.smtplib.SMTP")
def test_exception_graceful(mock_smtp, caplog):
    """SMTP error is logged but does not propagate."""
    mock_smtp.return_value.sendmail.side_effect = smtplib.SMTPException("fail")
    config = _make_config()

    with caplog.at_level(logging.ERROR):
        send_pipeline_summary_email(_make_run_data(), config)

    assert "Failed to send pipeline summary email" in caplog.text


@patch("pipeline.notifications.smtplib.SMTP")
def test_local_pdf_mode(mock_smtp):
    """Local PDF mode includes 'Local PDF' and omits Search/Database sections."""
    config = _make_config()
    run_data = _make_run_data(mode="local_pdf")

    send_pipeline_summary_email(run_data, config)

    body = mock_smtp.return_value.sendmail.call_args[0][2]
    assert "Local PDF" in body
    assert "Search Results" not in body
    assert "Database" not in body


@patch("pipeline.notifications.smtplib.SMTP")
def test_standard_dryrun(mock_smtp):
    """Dry run mode includes 'Dry Run' and omits Database section."""
    config = _make_config()
    run_data = _make_run_data(dry_run=True)

    send_pipeline_summary_email(run_data, config)

    body = mock_smtp.return_value.sendmail.call_args[0][2]
    assert "Dry Run" in body
    # Database section should not appear for dry-run
    assert ">Database<" not in body


@patch("pipeline.notifications.smtplib.SMTP")
def test_standard_live_with_database(mock_smtp):
    """Standard live mode with database result includes Database section."""
    config = _make_config()
    run_data = _make_run_data(database={"inserted": 3, "updated": 1})

    send_pipeline_summary_email(run_data, config)

    body = mock_smtp.return_value.sendmail.call_args[0][2]
    assert "Database" in body
    assert "Inserted" in body


@patch("pipeline.notifications.smtplib.SMTP")
def test_batch_warnings_in_body(mock_smtp):
    """Batch warnings appear in the email body."""
    config = _make_config()
    run_data = _make_run_data(batch_warnings=["High gene duplication detected"])

    send_pipeline_summary_email(run_data, config)

    body = mock_smtp.return_value.sendmail.call_args[0][2]
    assert "High gene duplication detected" in body


# ---------------------------------------------------------------------------
# Tests: _parse_recipients
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("input_str", "expected"),
    [
        ("alice@x.com", ["alice@x.com"]),
        ("alice@x.com, bob@y.com", ["alice@x.com", "bob@y.com"]),
        ("", []),
        ("  ,  , ", []),
        ("  alice@x.com  ", ["alice@x.com"]),
    ],
    ids=["single", "multiple", "empty", "whitespace-only", "padded"],
)
def test_parse_recipients(input_str, expected):
    assert _parse_recipients(input_str) == expected


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
