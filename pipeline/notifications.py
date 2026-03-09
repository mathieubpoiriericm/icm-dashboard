"""Pipeline notification dispatch via Apprise.

Replaces the legacy smtplib/email.mime layer with Apprise for
multi-channel delivery (ntfy push + Gmail SMTP backup) and Jinja2
for template rendering.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import apprise
import jinja2
from tenacity import retry, stop_after_attempt, wait_exponential

if TYPE_CHECKING:
    from pipeline.config import PipelineConfig
    from pipeline.report import PipelineRunData

logger = logging.getLogger(__name__)

# Jinja2 environment pointing at pipeline/templates/
_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_jinja_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=jinja2.select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _build_template_context(run_data: PipelineRunData) -> dict[str, Any]:
    """Extract template variables from PipelineRunData."""
    cfg = run_data.get("pipeline_config", {})
    mode = cfg.get("mode")
    is_standard = mode is None
    is_dry_run = cfg.get("dry_run", False)

    # Mode label
    if mode == "local_pdf":
        mode_label = "Local PDF"
    elif mode == "pmid_list":
        mode_label = "PMID List"
    else:
        mode_label = "Standard"
    if is_dry_run:
        mode_label += " (Dry Run)"

    # Database visibility
    show_database = (
        is_standard and not is_dry_run and run_data.get("database") is not None
    )

    # Cost string
    tu = run_data.get("token_usage", {})
    cost = tu.get("estimated_cost_usd")
    cost_str = f"${cost:.2f}" if cost is not None else "N/A"

    # Missing fulltext papers
    papers_detail = run_data.get("papers_detail", [])
    missing = sorted(
        [p for p in papers_detail if not p.get("fulltext")],
        key=lambda x: x["pmid"],
    )

    return {
        "mode_label": mode_label,
        "model": cfg.get("model", "N/A"),
        "duration": _format_duration(run_data.get("total_processing_time", 0.0)),
        "effort": cfg.get("effort", "N/A"),
        "show_days_back": is_standard,
        "days_back": cfg.get("days_back", "N/A"),
        "pdf_directory": cfg.get("pdf_directory"),
        "pmid_file": cfg.get("pmid_file"),
        "show_search": is_standard,
        "search": run_data.get("search", {}),
        "papers": run_data.get("papers", {}),
        "genes": run_data.get("genes", {}),
        "token_usage": tu,
        "cost_str": cost_str,
        "show_database": show_database,
        "database": run_data.get("database") or {},
        "batch_warnings": run_data.get("batch_validation_warnings", []),
        "missing_fulltext": missing,
    }


def _render_html(run_data: PipelineRunData) -> str:
    """Render the HTML email body from run data."""
    ctx = _build_template_context(run_data)
    template = _jinja_env.get_template("digest.html.j2")
    return template.render(ctx)


def _render_markdown(run_data: PipelineRunData) -> str:
    """Render the Markdown body for push notifications."""
    ctx = _build_template_context(run_data)
    template = _jinja_env.get_template("digest.md.j2")
    return template.render(ctx)


def _make_send_notification(config: PipelineConfig):
    """Return a Tenacity-wrapped sender function bound to *config*."""

    @retry(
        stop=stop_after_attempt(config.notify_max_retries),
        wait=wait_exponential(
            min=config.notify_retry_min_wait,
            max=config.notify_retry_max_wait,
        ),
        reraise=True,
    )
    def _send(
        title: str,
        body_text: str,
        body_html: str,
        notify_type: apprise.NotifyType = apprise.NotifyType.INFO,
    ) -> bool | None:
        ap = apprise.Apprise()
        for url in config.notify_urls.split(","):
            url = url.strip()
            if url:
                ap.add(url)
        return ap.notify(
            title=title,
            body=body_text,
            body_format=apprise.NotifyFormat.MARKDOWN,
            notify_type=notify_type,
        )

    return _send


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def send_pipeline_notification(
    run_data: PipelineRunData, config: PipelineConfig
) -> None:
    """Send a pipeline run summary via all configured Apprise channels.

    Non-fatal: errors are logged but never propagate.

    Args:
        run_data: Full run data dict from ``build_run_data`` / etc.
        config: Pipeline configuration with ``notify_urls``.
    """
    if not config.notify_urls:
        logger.warning(
            "Notification URLs not configured (PIPELINE_NOTIFY_URLS). "
            "Skipping notification."
        )
        return

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
    title = f"[SVD Pipeline] Run Summary \u2014 {mode_label} ({date_str})"

    body_md = _render_markdown(run_data)

    try:
        sender = _make_send_notification(config)
        sender(title=title, body_text=body_md, body_html="")
        logger.info("Pipeline notification sent successfully")
    except Exception as exc:
        logger.error(f"Failed to send pipeline notification: {exc}")


# Backwards-compatible alias
send_pipeline_summary_email = send_pipeline_notification
