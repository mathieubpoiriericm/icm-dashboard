"""Email notification utilities for the pipeline."""

from __future__ import annotations

import logging
import smtplib
import ssl
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pipeline.config import PipelineConfig
    from pipeline.report import PipelineRunData

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def send_pipeline_summary_email(
    run_data: PipelineRunData, config: PipelineConfig
) -> None:
    """Send a comprehensive pipeline run summary email.

    Args:
        run_data: Full run data dict from build_run_data / build_local_pdf_run_data
                  / build_pmid_run_data.
        config: Pipeline configuration containing email settings.
    """
    if not config.email_host:
        logger.warning(
            "Email configuration missing (PIPELINE_EMAIL_HOST). Skipping notification."
        )
        return

    recipients = _parse_recipients(config.email_admin)
    if not recipients:
        logger.warning("No valid email recipients configured. Skipping notification.")
        return

    # Determine mode label
    cfg = run_data.get("pipeline_config", {})
    mode = cfg.get("mode")
    if mode == "local_pdf":
        mode_label = "Local PDF"
    elif mode == "pmid_list":
        mode_label = "PMID List"
    else:
        mode_label = "Standard"
    if cfg.get("dry_run"):
        mode_label += " (Dry Run)"

    date_str = datetime.now().strftime("%Y-%m-%d")
    subject = f"[SVD Pipeline] Run Summary \u2014 {mode_label} ({date_str})"

    html_body = _build_html_body(run_data)
    plain_body = _build_plain_body(run_data)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = config.email_from
    msg["To"] = ", ".join(recipients)

    msg.attach(MIMEText(plain_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        with _build_smtp_connection(
            config.email_host,
            config.email_port,
            config.email_user,
            config.email_password,
        ) as server:
            server.sendmail(config.email_from, recipients, msg.as_string())

        logger.info(f"Pipeline summary email sent to {', '.join(recipients)}")

    except Exception as e:
        logger.error(f"Failed to send pipeline summary email: {e}")


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _parse_recipients(email_admin: str) -> list[str]:
    """Parse a comma-separated list of email addresses.

    Args:
        email_admin: Comma-separated email string.

    Returns:
        List of stripped, non-empty email addresses.
    """
    return [addr.strip() for addr in email_admin.split(",") if addr.strip()]


def _format_duration(seconds: float) -> str:
    """Format a duration in seconds to a human-readable string.

    Examples:
        5.3  -> "5.3s"
        125  -> "2m 5s"
        3661 -> "1h 1m 1s"
    """
    if seconds < 60:
        return f"{seconds:.1f}s"

    total_seconds = int(seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)

    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    return f"{minutes}m {secs}s"


@contextmanager
def _build_smtp_connection(
    host: str, port: int, user: str, password: str
) -> Generator[smtplib.SMTP | smtplib.SMTP_SSL]:
    """Create an SMTP connection with appropriate TLS handling.

    - Port 465: implicit TLS via SMTP_SSL
    - Port 587: STARTTLS upgrade
    - Other: plain SMTP (e.g. internal relay on port 25)
    """
    context = ssl.create_default_context()
    server: smtplib.SMTP | smtplib.SMTP_SSL

    if port == 465:
        server = smtplib.SMTP_SSL(host, port, context=context)
    else:
        server = smtplib.SMTP(host, port)
        if port == 587:
            server.starttls(context=context)

    try:
        if user and password:
            server.login(user, password)
        yield server
    finally:
        server.quit()


# ---------------------------------------------------------------------------
# Email body builders
# ---------------------------------------------------------------------------

_CSS = """\
    table { border-collapse: collapse; width: 100%; margin-bottom: 16px; }
    th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
    th { background-color: #f2f2f2; }
    tr:nth-child(even) { background-color: #f9f9f9; }
    .metric-table { width: auto; }
    .metric-table td:first-child { font-weight: bold; white-space: nowrap; }
    h2 { color: #2d287a; margin-top: 24px; }
    h3 { color: #667eea; }
"""


def _metric_rows_html(rows: list[tuple[str, Any]]) -> str:
    """Build HTML table rows from label/value pairs."""
    return "".join(
        f"<tr><td>{label}</td><td>{value}</td></tr>" for label, value in rows
    )


def _build_html_body(run_data: PipelineRunData) -> str:
    """Build the HTML email body from run data."""
    cfg = run_data.get("pipeline_config", {})
    mode = cfg.get("mode")
    is_standard = mode is None
    is_dry_run = cfg.get("dry_run", False)
    show_search = is_standard
    show_database = (
        is_standard and not is_dry_run and run_data.get("database") is not None
    )

    papers = run_data.get("papers", {})
    genes = run_data.get("genes", {})
    tu = run_data.get("token_usage", {})
    duration = run_data.get("total_processing_time", 0.0)

    sections: list[str] = []

    # --- Run Overview ---
    overview_rows: list[tuple[str, Any]] = []
    if mode == "local_pdf":
        overview_rows.append(("Mode", "Local PDF"))
    elif mode == "pmid_list":
        overview_rows.append(("Mode", "PMID List"))
    else:
        label = "Standard (Dry Run)" if is_dry_run else "Standard"
        overview_rows.append(("Mode", label))
    overview_rows.append(("Model", cfg.get("model", "N/A")))
    overview_rows.append(("Duration", _format_duration(duration)))
    overview_rows.append(("Effort", cfg.get("effort", "N/A")))
    if is_standard:
        overview_rows.append(("Days Back", cfg.get("days_back", "N/A")))
    if mode == "local_pdf":
        overview_rows.append(("PDF Directory", cfg.get("pdf_directory", "N/A")))
    if mode == "pmid_list":
        overview_rows.append(("PMID File", cfg.get("pmid_file", "N/A")))

    sections.append(
        f"<h2>Run Overview</h2>"
        f"<table class='metric-table'>{_metric_rows_html(overview_rows)}</table>"
    )

    # --- Search Results (standard mode only) ---
    if show_search:
        search = run_data.get("search", {})
        search_rows = [
            ("PMIDs Found", search.get("pmids_found", 0)),
            ("New PMIDs", search.get("pmids_new", 0)),
            ("Skipped (already processed)", search.get("pmids_skipped", 0)),
        ]
        sections.append(
            f"<h2>Search Results</h2>"
            f"<table class='metric-table'>{_metric_rows_html(search_rows)}</table>"
        )

    # --- Papers ---
    fulltext_rate = papers.get("fulltext_rate", 0)
    papers_rows = [
        ("Processed", papers.get("processed", 0)),
        ("Full Text", papers.get("fulltext", 0)),
        ("Abstract Only", papers.get("abstract_only", 0)),
        ("Full Text Rate", f"{fulltext_rate:.1%}"),
        ("Failed", papers.get("failed", 0)),
    ]
    sections.append(
        f"<h2>Papers</h2>"
        f"<table class='metric-table'>{_metric_rows_html(papers_rows)}</table>"
    )

    # --- Genes ---
    acceptance_rate = genes.get("acceptance_rate", 0)
    genes_rows = [
        ("Extracted", genes.get("extracted", 0)),
        ("Validated", genes.get("validated", 0)),
        ("Rejected", genes.get("rejected", 0)),
        ("Acceptance Rate", f"{acceptance_rate:.1%}"),
    ]
    sections.append(
        f"<h2>Genes</h2>"
        f"<table class='metric-table'>{_metric_rows_html(genes_rows)}</table>"
    )

    # --- Token Usage ---
    total_tokens = tu.get("total_tokens", 0)
    if total_tokens > 0:
        cost = tu.get("estimated_cost_usd")
        cost_str = f"${cost:.2f}" if cost is not None else "N/A"
        cache_hit_rate = tu.get("cache_hit_rate", 0)
        token_rows = [
            ("Input Tokens", f"{tu.get('input_tokens', 0):,}"),
            ("Output Tokens", f"{tu.get('output_tokens', 0):,}"),
            ("Cache Read", f"{tu.get('cache_read_input_tokens', 0):,}"),
            ("Cache Created", f"{tu.get('cache_creation_input_tokens', 0):,}"),
            ("Cache Hit Rate", f"{cache_hit_rate:.1%}"),
            ("Total Tokens", f"{total_tokens:,}"),
            ("Estimated Cost", cost_str),
        ]
        sections.append(
            f"<h2>Token Usage</h2>"
            f"<table class='metric-table'>{_metric_rows_html(token_rows)}</table>"
        )

    # --- Database ---
    if show_database:
        db = run_data.get("database")
        assert db is not None  # guaranteed by show_database guard
        db_rows = [
            ("Inserted", db.get("inserted", 0)),
            ("Updated", db.get("updated", 0)),
        ]
        sections.append(
            f"<h2>Database</h2>"
            f"<table class='metric-table'>{_metric_rows_html(db_rows)}</table>"
        )

    # --- Batch Warnings ---
    batch_warnings = run_data.get("batch_validation_warnings", [])
    if batch_warnings:
        items = "".join(f"<li>{w}</li>" for w in batch_warnings)
        sections.append(f"<h2>Batch Validation Warnings</h2><ul>{items}</ul>")

    # --- Missing Full-Text ---
    papers_detail = run_data.get("papers_detail", [])
    missing = [p for p in papers_detail if not p.get("fulltext")]
    if missing:
        missing.sort(key=lambda x: x["pmid"])
        rows_html = ""
        for p in missing:
            pmid = p["pmid"]
            error = p.get("error")
            status = f"Error: {error}" if error else "Abstract Only"
            source = p.get("source", "unknown")
            url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
            rows_html += (
                f"<tr>"
                f"<td><a href='{url}'>{pmid}</a></td>"
                f"<td>{status}</td>"
                f"<td>{source}</td>"
                f"</tr>"
            )
        sections.append(
            f"<h2>Missing Full-Text Papers</h2>"
            f"<table>"
            f"<thead><tr><th>PMID</th><th>Status</th><th>Source</th></tr></thead>"
            f"<tbody>{rows_html}</tbody>"
            f"</table>"
            f"<p><em>Please review these papers manually.</em></p>"
        )

    body = "\n".join(sections)
    return f"<html><head><style>{_CSS}</style></head><body>{body}</body></html>"


def _build_plain_body(run_data: PipelineRunData) -> str:
    """Build the plain-text email body from run data."""
    cfg = run_data.get("pipeline_config", {})
    mode = cfg.get("mode")
    is_standard = mode is None
    is_dry_run = cfg.get("dry_run", False)
    show_search = is_standard
    show_database = (
        is_standard and not is_dry_run and run_data.get("database") is not None
    )

    papers = run_data.get("papers", {})
    genes = run_data.get("genes", {})
    tu = run_data.get("token_usage", {})
    duration = run_data.get("total_processing_time", 0.0)

    lines: list[str] = []

    # --- Run Overview ---
    lines.append("RUN OVERVIEW")
    lines.append("=" * 40)
    if mode == "local_pdf":
        lines.append("Mode: Local PDF")
    elif mode == "pmid_list":
        lines.append("Mode: PMID List")
    else:
        label = "Standard (Dry Run)" if is_dry_run else "Standard"
        lines.append(f"Mode: {label}")
    lines.append(f"Model: {cfg.get('model', 'N/A')}")
    lines.append(f"Duration: {_format_duration(duration)}")
    lines.append(f"Effort: {cfg.get('effort', 'N/A')}")
    if is_standard:
        lines.append(f"Days Back: {cfg.get('days_back', 'N/A')}")
    if mode == "local_pdf":
        lines.append(f"PDF Directory: {cfg.get('pdf_directory', 'N/A')}")
    if mode == "pmid_list":
        lines.append(f"PMID File: {cfg.get('pmid_file', 'N/A')}")
    lines.append("")

    # --- Search Results ---
    if show_search:
        search = run_data.get("search", {})
        lines.append("SEARCH RESULTS")
        lines.append("-" * 40)
        lines.append(f"PMIDs Found: {search.get('pmids_found', 0)}")
        lines.append(f"New PMIDs: {search.get('pmids_new', 0)}")
        lines.append(f"Skipped: {search.get('pmids_skipped', 0)}")
        lines.append("")

    # --- Papers ---
    fulltext_rate = papers.get("fulltext_rate", 0)
    lines.append("PAPERS")
    lines.append("-" * 40)
    lines.append(f"Processed: {papers.get('processed', 0)}")
    lines.append(f"Full Text: {papers.get('fulltext', 0)}")
    lines.append(f"Abstract Only: {papers.get('abstract_only', 0)}")
    lines.append(f"Full Text Rate: {fulltext_rate:.1%}")
    lines.append(f"Failed: {papers.get('failed', 0)}")
    lines.append("")

    # --- Genes ---
    acceptance_rate = genes.get("acceptance_rate", 0)
    lines.append("GENES")
    lines.append("-" * 40)
    lines.append(f"Extracted: {genes.get('extracted', 0)}")
    lines.append(f"Validated: {genes.get('validated', 0)}")
    lines.append(f"Rejected: {genes.get('rejected', 0)}")
    lines.append(f"Acceptance Rate: {acceptance_rate:.1%}")
    lines.append("")

    # --- Token Usage ---
    total_tokens = tu.get("total_tokens", 0)
    if total_tokens > 0:
        cost = tu.get("estimated_cost_usd")
        cost_str = f"${cost:.2f}" if cost is not None else "N/A"
        cache_hit_rate = tu.get("cache_hit_rate", 0)
        lines.append("TOKEN USAGE")
        lines.append("-" * 40)
        lines.append(f"Input Tokens: {tu.get('input_tokens', 0):,}")
        lines.append(f"Output Tokens: {tu.get('output_tokens', 0):,}")
        lines.append(f"Cache Read: {tu.get('cache_read_input_tokens', 0):,}")
        lines.append(f"Cache Created: {tu.get('cache_creation_input_tokens', 0):,}")
        lines.append(f"Cache Hit Rate: {cache_hit_rate:.1%}")
        lines.append(f"Total Tokens: {total_tokens:,}")
        lines.append(f"Estimated Cost: {cost_str}")
        lines.append("")

    # --- Database ---
    if show_database:
        db = run_data.get("database")
        assert db is not None  # guaranteed by show_database guard
        lines.append("DATABASE")
        lines.append("-" * 40)
        lines.append(f"Inserted: {db.get('inserted', 0)}")
        lines.append(f"Updated: {db.get('updated', 0)}")
        lines.append("")

    # --- Batch Warnings ---
    batch_warnings = run_data.get("batch_validation_warnings", [])
    if batch_warnings:
        lines.append("BATCH VALIDATION WARNINGS")
        lines.append("-" * 40)
        for w in batch_warnings:
            lines.append(f"  - {w}")
        lines.append("")

    # --- Missing Full-Text ---
    papers_detail = run_data.get("papers_detail", [])
    missing = [p for p in papers_detail if not p.get("fulltext")]
    if missing:
        missing.sort(key=lambda x: x["pmid"])
        lines.append("MISSING FULL-TEXT PAPERS")
        lines.append("-" * 40)
        lines.append(f"{'PMID':<10} | {'Status':<15} | Source")
        lines.append(f"{'-' * 10}-+-{'-' * 15}-+------")
        for p in missing:
            pmid = p["pmid"]
            error = p.get("error")
            status = "Error" if error else "Abstract"
            source = p.get("source", "unknown")
            lines.append(f"{pmid:<10} | {status:<15} | {source}")
        lines.append("")
        lines.append("Please review these papers manually.")

    return "\n".join(lines) + "\n"
