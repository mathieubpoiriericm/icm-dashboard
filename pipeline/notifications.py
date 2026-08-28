"""Pipeline notification dispatch via Apprise.

Sends pipeline run digests via Apprise (multi-channel) using a
Jinja2-rendered Markdown body.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import apprise
import jinja2
from apprise import NotifyFormat, NotifyType
from tenacity import retry, stop_after_attempt, wait_exponential

if TYPE_CHECKING:
    from pipeline.config import PipelineConfig
    from pipeline.report import PipelineRunData

logger = logging.getLogger(__name__)

# Jinja2 environment pointing at pipeline/templates/
_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_jinja_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=False,
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


_MODE_LABELS: dict[str | None, str] = {
    "local_pdf": "Local PDF",
    "pmid_list": "PMID List",
    "combined": "Combined",
    None: "Standard",
}


def _mode_label(cfg: dict[str, Any]) -> str:
    """Human-readable label for a pipeline run mode."""
    label = _MODE_LABELS.get(cfg.get("mode"), "Standard")
    if cfg.get("dry_run"):
        label += " (Dry Run)"
    return label


def _build_template_context(run_data: PipelineRunData) -> dict[str, Any]:
    """Extract template variables from PipelineRunData."""
    cfg = run_data.get("pipeline_config", {})
    is_standard = cfg.get("mode") is None
    mode_label = _mode_label(cfg)

    # Combined / multi-pipeline invocations carry a ``pipelines`` list.
    # Cost / papers / genes aren't always applicable in that shape, so we
    # fall back to zeros and only show sections the template is guarded
    # against.
    pipelines = run_data.get("pipelines") or []

    # Database visibility
    show_database = (
        is_standard
        and not cfg.get("dry_run", False)
        and run_data.get("database") is not None
    )

    # Cost string
    tu = run_data.get("token_usage", {})
    cost = tu.get("estimated_cost_usd")
    cost_str = f"${cost:.2f}" if cost is not None else "N/A"

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
        "pipelines": pipelines,
    }


def _render_markdown(run_data: PipelineRunData) -> str:
    """Render the Markdown body for push notifications."""
    template = _jinja_env.get_template("digest.md.j2")
    return template.render(_build_template_context(run_data))


def _make_send_notification(
    config: PipelineConfig,
) -> Callable[[str, str], bool | None]:
    """Return a Tenacity-wrapped sender function bound to *config*."""
    ap = apprise.Apprise()
    for url in map(str.strip, config.notify_urls.split(",")):
        if url:
            ap.add(url)

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
        notify_type: NotifyType = NotifyType.INFO,
    ) -> bool | None:
        return ap.notify(
            title=title,
            body=body_text,
            body_format=NotifyFormat.MARKDOWN,
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

    mode_label = _mode_label(run_data.get("pipeline_config", {}))
    # UTC matches the report body's `timestamp` field \u2014 otherwise the
    # notification title can show a different date than the run's own log.
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    title = f"[SVD Pipeline] Run Summary \u2014 {mode_label} ({date_str})"

    body_md = _render_markdown(run_data)

    try:
        sender = _make_send_notification(config)
        sender(title, body_md)
        logger.info("Pipeline notification sent successfully")
    except Exception as exc:
        logger.error(f"Failed to send pipeline notification: {exc}")
