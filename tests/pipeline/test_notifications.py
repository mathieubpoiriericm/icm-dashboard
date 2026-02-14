
import logging
from typing import cast
from unittest.mock import patch

from pipeline.config import PipelineConfig
from pipeline.notifications import send_missing_fulltext_email
from pipeline.report import PaperSummary


def test_send_missing_fulltext_email_no_config(caplog):
    """Test that function returns early if email host is not configured."""
    config = PipelineConfig()
    # Ensure empty host
    config.email_host = ""
    
    with caplog.at_level(logging.WARNING):
        send_missing_fulltext_email([], config)
    
    assert "Email configuration missing" in caplog.text

def test_send_missing_fulltext_email_no_missing_papers(caplog):
    """Test that function returns early if no missing fulltext papers."""
    config = PipelineConfig()
    config.email_host = "smtp.example.com"
    
    papers = cast(
        list["PaperSummary"],
        [
            {"pmid": "123", "fulltext": True, "source": "pmc"},
            {"pmid": "456", "fulltext": True, "source": "unpaywall"},
        ],
    )
    
    with caplog.at_level(logging.INFO):
        send_missing_fulltext_email(papers, config)
    
    assert "No missing full-text papers" in caplog.text

@patch("smtplib.SMTP")
def test_send_missing_fulltext_email_success(mock_smtp, caplog):
    """Test successful email sending."""
    config = PipelineConfig()
    config.email_host = "smtp.example.com"
    config.email_port = 587
    config.email_user = "user"
    config.email_password = "password"
    config.email_from = "sender@example.com"
    config.email_admin = "admin@example.com"
    
    papers = cast(
        list["PaperSummary"],
        [
            {"pmid": "123", "fulltext": False, "source": "none", "error": "No text"},
            {"pmid": "456", "fulltext": True, "source": "pmc"},
        ],
    )
    
    with caplog.at_level(logging.INFO):
        send_missing_fulltext_email(papers, config)
    
    assert "Preparing email notification" in caplog.text
    assert "Notification email sent" in caplog.text
    
    # Verify SMTP calls
    mock_smtp.assert_called_with("smtp.example.com", 587)
    instance = mock_smtp.return_value.__enter__.return_value
    instance.starttls.assert_called()
    instance.login.assert_called_with("user", "password")
    instance.sendmail.assert_called()
