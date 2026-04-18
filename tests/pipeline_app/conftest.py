"""Shared fixtures for pipeline_app tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect all config file paths to a temp directory."""
    monkeypatch.setattr("pipeline_app.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("pipeline_app.config.CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr("pipeline_app.config.HISTORY_PATH", tmp_path / "history.json")
    monkeypatch.setattr("pipeline_app.config.PRESETS_PATH", tmp_path / "presets.json")
    monkeypatch.setattr(
        "pipeline_app.config.TUNING_CONFIG_PATH", tmp_path / "tuning_config.json"
    )
    return tmp_path


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Create a temp directory with the project structure markers.

    validate_project_root() requires pipeline/main.py to exist.
    """
    (tmp_path / "pipeline").mkdir()
    (tmp_path / "pipeline" / "main.py").write_text("")
    return tmp_path
