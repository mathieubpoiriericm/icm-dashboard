"""Email notification utilities for the pipeline."""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pipeline.config import PipelineConfig
    from pipeline.report import PaperSummary

logger = logging.getLogger(__name__)


def send_missing_fulltext_email(
    papers: list[PaperSummary], config: PipelineConfig
) -> None:
    """Send an email to the admin listing papers with missing full text.

    Args:
        papers: List of paper summaries from the current run.
        config: Pipeline configuration containing email settings.
    """
    if not config.email_host:
        logger.warning(
            "Email configuration missing (PIPELINE_EMAIL_HOST). Skipping notification."
        )
        return

    # Filter for papers where full text was not retrieved (abstract only or errors)
    # We include papers with errors if they resulted in no full text.
    missing_fulltext_papers = [p for p in papers if not p.get("fulltext")]

    if not missing_fulltext_papers:
        logger.info("No missing full-text papers to report.")
        return

    logger.info(
        f"Preparing email notification for {len(missing_fulltext_papers)} "
        "papers with missing full text."
    )

    # Sort by PMID for cleaner display
    missing_fulltext_papers.sort(key=lambda x: x["pmid"])

    # Build email content
    subject = (
        f"[SVD Dashboard] Missing Full-Text Report "
        f"({len(missing_fulltext_papers)} papers)"
    )
    
    # HTML Body
    html_rows = []
    text_rows = []
    
    for p in missing_fulltext_papers:
        pmid = p["pmid"]
        source = p.get("source", "unknown")
        error = p.get("error")
        
        # Determine status
        if error:
            status = f"Error: {error}"
            status_short = "Error"
        else:
            status = "Abstract Only"
            status_short = "Abstract"

        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        
        # HTML row
        html_rows.append(
            f"<tr>"
            f"<td><a href='{url}'>{pmid}</a></td>"
            f"<td>{status}</td>"
            f"<td>{source}</td>"
            f"</tr>"
        )
        
        # Plain text row
        text_rows.append(f"{pmid:<10} | {status_short:<15} | {source}")

    html_body = f"""
    <html>
    <head>
        <style>
            table {{ border-collapse: collapse; width: 100%; }}
            th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
            th {{ background-color: #f2f2f2; }}
            tr:nth-child(even) {{ background-color: #f9f9f9; }}
        </style>
    </head>
    <body>
        <h2>Missing Full-Text Papers</h2>
        <p>
            The following papers could not be retrieved in full text
            during the recent pipeline run:
        </p>
        <table>
            <thead>
                <tr>
                    <th>PMID</th>
                    <th>Status</th>
                    <th>Source</th>
                </tr>
            </thead>
            <tbody>
                {"".join(html_rows)}
            </tbody>
        </table>
        <p><em>Please review these papers manually.</em></p>
    </body>
    </html>
    """

    plain_body = (
        "Missing Full-Text Papers Report\n"
        "===============================\n\n"
        "The following papers could not be retrieved in full text:\n\n"
        f"{'PMID':<10} | {'Status':<15} | Source\n"
        f"{'-'*10}-+-{'-'*15}-+------\n"
        + "\n".join(text_rows)
        + "\n\nPlease review these papers manually.\n"
    )

    # Construct message
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = config.email_from
    msg["To"] = config.email_admin

    part1 = MIMEText(plain_body, "plain")
    part2 = MIMEText(html_body, "html")

    msg.attach(part1)
    msg.attach(part2)

    # Send email
    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(config.email_host, config.email_port) as server:
            # Upgrade connection to secure if supported (STARTTLS)
            # Port 465 usually uses SSLWrapper, 587 uses STARTTLS.
            # We assume STARTTLS for typical 587 usage.
            if config.email_port == 587:
                server.starttls(context=context)
            
            if config.email_user and config.email_password:
                server.login(config.email_user, config.email_password)
            
            server.sendmail(config.email_from, config.email_admin, msg.as_string())
        
        logger.info(f"Notification email sent to {config.email_admin}")

    except Exception as e:
        logger.error(f"Failed to send notification email: {e}")
