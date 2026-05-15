from pipeline.config import PipelineConfig


def test_notification_config_defaults(monkeypatch):
    monkeypatch.delenv("PIPELINE_NOTIFY_URLS", raising=False)
    monkeypatch.delenv("PIPELINE_EVENT_DB_PATH", raising=False)
    monkeypatch.delenv("PIPELINE_NOTIFY_MAX_RETRIES", raising=False)
    monkeypatch.delenv("PIPELINE_NOTIFY_RETRY_MIN_WAIT", raising=False)
    monkeypatch.delenv("PIPELINE_NOTIFY_RETRY_MAX_WAIT", raising=False)

    cfg = PipelineConfig()
    assert cfg.notify_urls == ""
    assert cfg.event_db_path.endswith("events.db")
    assert cfg.notify_max_retries == 3
    assert cfg.notify_retry_min_wait == 4.0
    assert cfg.notify_retry_max_wait == 30.0
