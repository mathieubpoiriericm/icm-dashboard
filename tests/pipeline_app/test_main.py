"""Tests for the main entrypoint's graceful-shutdown handler."""

from __future__ import annotations

import asyncio
import logging
import time
from unittest.mock import AsyncMock

import pytest
from pipeline_app.main import SHUTDOWN_TIMEOUT_SECONDS, build_shutdown_handler


class TestShutdownHandler:
    @pytest.mark.asyncio
    async def test_calls_both_cancels(self):
        lock = AsyncMock()
        runner = AsyncMock()
        handler = build_shutdown_handler(lock, runner)
        await handler()
        assert runner.cancel.await_count == 1
        assert lock.cancel.await_count == 1

    @pytest.mark.asyncio
    async def test_suppresses_exceptions_from_tuning_runner(self):
        lock = AsyncMock()
        runner = AsyncMock()
        runner.cancel.side_effect = RuntimeError("boom")
        handler = build_shutdown_handler(lock, runner)
        # Suppressed — lock.cancel() still runs after the first cancel raises.
        await handler()
        assert runner.cancel.await_count == 1
        assert lock.cancel.await_count == 1

    @pytest.mark.asyncio
    async def test_suppresses_exceptions_from_lock(self):
        lock = AsyncMock()
        lock.cancel.side_effect = OSError("gone")
        runner = AsyncMock()
        handler = build_shutdown_handler(lock, runner)
        await handler()
        assert runner.cancel.await_count == 1
        assert lock.cancel.await_count == 1

    @pytest.mark.asyncio
    async def test_timeout_logs_warning_and_returns(self, caplog):
        # Lock whose cancel blocks forever — simulates a stubborn child.
        async def _never_returns(*_args, **_kwargs):
            await asyncio.sleep(60.0)

        lock = AsyncMock()
        lock.cancel.side_effect = _never_returns
        runner = AsyncMock()
        handler = build_shutdown_handler(lock, runner, timeout=0.2)
        started = time.monotonic()
        with caplog.at_level(logging.WARNING, logger="pipeline_app.main"):
            await handler()
        elapsed = time.monotonic() - started
        assert elapsed < 1.0
        assert any(
            "Shutdown timeout after" in r.message
            and "subprocess may orphan" in r.message
            for r in caplog.records
        )

    def test_timeout_constant_is_reasonable(self):
        # SubprocessLock.cancel() worst case is ~10s (SIGTERM 5s + SIGKILL 5s).
        # Timeout must exceed that so the normal path isn't prematurely truncated.
        assert SHUTDOWN_TIMEOUT_SECONDS >= 11.0
