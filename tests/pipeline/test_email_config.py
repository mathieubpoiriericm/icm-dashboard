
from pipeline.config import PipelineConfig


def test_email_config_defaults():
    cfg = PipelineConfig()
    assert cfg.email_host == ""
    assert cfg.email_port == 587
    assert cfg.email_user == ""
    assert cfg.email_password == ""
    assert cfg.email_from == "noreply@svd-dashboard.org"
    assert cfg.email_admin == "mathieu.poirier@icm-institute.org"

def test_email_config_overrides(monkeypatch):
    monkeypatch.setenv("PIPELINE_EMAIL_HOST", "smtp.custom.com")
    monkeypatch.setenv("PIPELINE_EMAIL_PORT", "25")
    monkeypatch.setenv("PIPELINE_EMAIL_USER", "custom_user")
    monkeypatch.setenv("PIPELINE_EMAIL_PASSWORD", "secret")
    monkeypatch.setenv("PIPELINE_EMAIL_FROM", "bot@custom.com")
    monkeypatch.setenv("PIPELINE_EMAIL_ADMIN", "boss@custom.com")
    
    cfg = PipelineConfig()
    assert cfg.email_host == "smtp.custom.com"
    assert cfg.email_port == 25
    assert cfg.email_user == "custom_user"
    assert cfg.email_password == "secret"
    assert cfg.email_from == "bot@custom.com"
    assert cfg.email_admin == "boss@custom.com"
