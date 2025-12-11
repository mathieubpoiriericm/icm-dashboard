from pipeline.config import PipelineConfig


def test_notification_config_defaults(monkeypatch):
    monkeypatch.delenv("PIPELINE_NOTIFY_URLS", raising=False)
    monkeypatch.delenv("PIPELINE_HEALTHCHECK_URL", raising=False)
    monkeypatch.delenv("PIPELINE_EVENT_DB_PATH", raising=False)
    monkeypatch.delenv("PIPELINE_NOTIFY_MAX_RETRIES", raising=False)
    monkeypatch.delenv("PIPELINE_NOTIFY_RETRY_MIN_WAIT", raising=False)
    monkeypatch.delenv("PIPELINE_NOTIFY_RETRY_MAX_WAIT", raising=False)

    cfg = PipelineConfig()
    assert cfg.notify_urls == ""
    assert cfg.healthcheck_url == ""
    assert cfg.event_db_path.endswith("events.db")
    assert cfg.notify_max_retries == 3
    assert cfg.notify_retry_min_wait == 4.0
    assert cfg.notify_retry_max_wait == 30.0


def test_notification_config_overrides(monkeypatch):
    monkeypatch.setenv("PIPELINE_NOTIFY_URLS", "ntfy://localhost/test")
    monkeypatch.setenv("PIPELINE_HEALTHCHECK_URL", "http://hc.local/ping/abc")
    monkeypatch.setenv("PIPELINE_EVENT_DB_PATH", "/tmp/test_events.db")
    monkeypatch.setenv("PIPELINE_NOTIFY_MAX_RETRIES", "5")
    monkeypatch.setenv("PIPELINE_NOTIFY_RETRY_MIN_WAIT", "2.0")
    monkeypatch.setenv("PIPELINE_NOTIFY_RETRY_MAX_WAIT", "60.0")

    cfg = PipelineConfig()
    assert cfg.notify_urls == "ntfy://localhost/test"
    assert cfg.healthcheck_url == "http://hc.local/ping/abc"
    assert cfg.event_db_path == "/tmp/test_events.db"
    assert cfg.notify_max_retries == 5
    assert cfg.notify_retry_min_wait == 2.0
    assert cfg.notify_retry_max_wait == 60.0


def test_legacy_email_config_still_works(monkeypatch):
    """Legacy email_* fields still exist for backwards compatibility."""
    monkeypatch.delenv("PIPELINE_EMAIL_HOST", raising=False)
    monkeypatch.delenv("PIPELINE_EMAIL_PORT", raising=False)
    monkeypatch.delenv("PIPELINE_EMAIL_FROM", raising=False)
    monkeypatch.delenv("PIPELINE_EMAIL_ADMIN", raising=False)

    cfg = PipelineConfig()
    assert cfg.email_host == ""
    assert cfg.email_port == 587
    assert cfg.email_from == "noreply@svd-dashboard.org"
    assert cfg.email_admin == ""
