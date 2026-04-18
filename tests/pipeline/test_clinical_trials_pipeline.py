"""Tests for pipeline.main.run_clinical_trials_pipeline."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from pipeline.cache_utils import SyncResult
from pipeline.config import PipelineConfig
from pipeline.main import run_clinical_trials_pipeline


class TestRunClinicalTrialsPipeline:
    async def test_disabled_config_returns_skipped(self, mocker):
        """ct_enabled=False short-circuits to a skipped summary."""
        mocker.patch("pipeline.main.ping_start")
        mocker.patch("pipeline.main.ping_success")
        mocker.patch("pipeline.main.close_ctg_client", new_callable=AsyncMock)
        mock_sync = mocker.patch(
            "pipeline.main.sync_clinical_trials", new_callable=AsyncMock
        )

        config = PipelineConfig()
        config.ct_enabled = False

        summary = await run_clinical_trials_pipeline(config=config)

        assert summary["name"] == "clinical_trials"
        assert summary["status"] == "skipped"
        assert summary["metrics"] == {"fetched": 0, "cached": 0, "failed": 0}
        mock_sync.assert_not_awaited()

    async def test_success_reports_metrics(self, mocker):
        """Successful sync returns status=ok and the underlying counts."""
        mocker.patch("pipeline.main.ping_start")
        mocker.patch("pipeline.main.ping_success")
        mocker.patch("pipeline.main.close_ctg_client", new_callable=AsyncMock)
        mocker.patch("pipeline.main.Database.set_config")
        mocker.patch("pipeline.main.Database.close", new_callable=AsyncMock)

        mocker.patch(
            "pipeline.main.sync_clinical_trials",
            new_callable=AsyncMock,
            return_value=SyncResult(fetched=12, cached=10, failed=0, errors=[]),
        )

        config = PipelineConfig()
        config.ct_enabled = True

        summary = await run_clinical_trials_pipeline(config=config)

        assert summary["name"] == "clinical_trials"
        assert summary["status"] == "ok"
        assert summary["metrics"] == {"fetched": 12, "cached": 10, "failed": 0}
        assert summary["errors"] == []

    async def test_errors_mark_status_failed(self, mocker):
        """Non-empty errors promote status to 'failed' without raising."""
        mocker.patch("pipeline.main.ping_start")
        mocker.patch("pipeline.main.ping_success")
        mocker.patch("pipeline.main.close_ctg_client", new_callable=AsyncMock)
        mocker.patch("pipeline.main.Database.set_config")
        mocker.patch("pipeline.main.Database.close", new_callable=AsyncMock)

        mocker.patch(
            "pipeline.main.sync_clinical_trials",
            new_callable=AsyncMock,
            return_value=SyncResult(
                fetched=5, cached=0, failed=5, errors=["CTG upsert boom"]
            ),
        )

        config = PipelineConfig()
        config.ct_enabled = True

        summary = await run_clinical_trials_pipeline(config=config)

        assert summary["status"] == "failed"
        assert summary["errors"] == ["CTG upsert boom"]

    async def test_exception_from_sync_propagates(self, mocker):
        """Unhandled exceptions from sync_clinical_trials are re-raised."""
        mock_ping_fail = mocker.patch("pipeline.main.ping_failure")
        mocker.patch("pipeline.main.ping_start")
        mocker.patch("pipeline.main.close_ctg_client", new_callable=AsyncMock)
        mocker.patch("pipeline.main.Database.set_config")
        mocker.patch("pipeline.main.Database.close", new_callable=AsyncMock)

        mocker.patch(
            "pipeline.main.sync_clinical_trials",
            new_callable=AsyncMock,
            side_effect=RuntimeError("CTG API unreachable"),
        )

        config = PipelineConfig()
        config.ct_enabled = True

        with pytest.raises(RuntimeError, match="CTG API unreachable"):
            await run_clinical_trials_pipeline(config=config)

        # With manage_lifecycle=True (default), a failure ping is emitted.
        mock_ping_fail.assert_called_once()

    async def test_manage_lifecycle_false_skips_pings(self, mocker):
        """Dispatcher path (manage_lifecycle=False) must not touch healthcheck."""
        mock_start = mocker.patch("pipeline.main.ping_start")
        mock_success = mocker.patch("pipeline.main.ping_success")
        mocker.patch("pipeline.main.close_ctg_client", new_callable=AsyncMock)
        mocker.patch("pipeline.main.Database.set_config")
        mocker.patch("pipeline.main.Database.close", new_callable=AsyncMock)

        mocker.patch(
            "pipeline.main.sync_clinical_trials",
            new_callable=AsyncMock,
            return_value=SyncResult(fetched=1, cached=1, failed=0, errors=[]),
        )

        config = PipelineConfig()
        config.ct_enabled = True

        await run_clinical_trials_pipeline(config=config, manage_lifecycle=False)

        mock_start.assert_not_called()
        mock_success.assert_not_called()
